"""Синтетические данные для знакомства с Data Room. Только для разработки (DJANGO_DEBUG=1).

Создаёт вымышленную компанию, комнаты, документы, посетителей с разными правами, приглашение, заявку и журнал.
Повторный запуск ничего не дублирует. Существующие данные не изменяются и не удаляются.
Реальных имён, адресов и документов здесь нет: адреса в зоне example.com.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import events
from apps.audit.models import AuditEvent
from apps.core import demo_files as files
from apps.core.models import InstanceSettings
from apps.invites import services as invite_services
from apps.invites.models import AccessRequest, Invitation
from apps.rooms import services
from apps.rooms.models import AccessGrant, Document, Folder, NdaAcceptance, NdaText, NdaVersion, PageTime, Room

DEMO_PASSWORD = "Demo-Dataroom-2026!"

INVESTORS = [
    # email, имя, язык
    ("investor1@example.com", "Иван Петров", "ru"),
    ("investor2@example.com", "Мария Орлова", "ru"),
    ("investor3@example.com", "Thomas Weber", "de"),
    ("investor4@example.com", "Олена Коваль", "uk"),
]

NDA_TEXTS = {
    "ru": "Соглашение о неразглашении (демонстрационный текст). Вы обязуетесь не передавать материалы комнаты третьим лицам.",
    "en": "Non-disclosure agreement (demo text). You agree not to share the room materials with third parties.",
    "de": "Geheimhaltungsvereinbarung (Demotext). Sie verpflichten sich, die Raumunterlagen nicht an Dritte weiterzugeben.",
    "uk": "Угода про нерозголошення (демонстраційний текст). Ви зобов’язуєтеся не передавати матеріали кімнати третім особам.",
}

QUARTERS = [("Q1", 410), ("Q2", 520), ("Q3", 690), ("Q4", 880)]


def _pdf(title, *pages):
    return files.make_pdf(title, [p if isinstance(p, dict) else {"lines": p} for p in pages])


def documents():
    """(ключ комнаты, ключ папки, название, имя файла, байты)."""
    deck = _pdf(
        "Investor presentation",
        ["Nordlicht Robotics builds autonomous warehouse robots.", "", "Problem: manual picking is slow and error-prone.",
         "Solution: a fleet of small robots coordinated by one planner.", "Status: 12 pilot sites, 3 paying customers."],
        ["Market", "", "Warehouse automation grows 15% a year (illustrative).", "Target: mid-size logistics operators.",
         "Entry: robots as a service with monthly pricing."],
        {"lines": ["Revenue plan (synthetic, kEUR)", ""], "bars": QUARTERS},
        ["Team", "", "Founders: operations, robotics, software.", "14 employees, 9 engineers."],
        ["The ask", "", "Seed round: EUR 2.5M (fictional).", "Use of funds: product, pilots, certification."],
    )
    report = _pdf(
        "Q3 financial report",
        ["Revenue grew 33% quarter over quarter (synthetic).", "", "Recurring share: 71%.", "Gross margin: 48%."],
        {"lines": ["Revenue by quarter (kEUR)", ""], "bars": QUARTERS},
        ["Costs", "", "Hardware: 31%", "Personnel: 49%", "Cloud and tools: 6%", "Other: 14%"],
        ["Cash position", "", "Cash at end of quarter: EUR 640k (synthetic).", "Runway: 11 months."],
    )
    return [
        ("overview", "summary", "Презентация для инвесторов", "investor-presentation.pdf", deck),
        ("overview", "financials", "Отчёт за 3 квартал", "q3-report.pdf", report),
        ("overview", "financials", "Финансовая модель", "financial-model.xlsx", files.make_xlsx(
            [["Quarter", "Revenue kEUR", "Costs kEUR"]] + [[q, v, int(v * 0.8)] for q, v in QUARTERS])),
        ("overview", "team", "Структура команды", "team.pdf", _pdf(
            "Team structure", ["Operations: 3", "Robotics: 5", "Software: 4", "Sales and support: 2"], ["Hiring plan: +6 in 12 months."])),
        ("overview", "product", "Технологический обзор", "technology.pdf", _pdf(
            "Technology overview", ["Robots: 40 kg payload, 8 h battery.", "Planner: central, replans every second."],
            ["Safety: certified stop in 0.3 s (illustrative).", "Data: all stored in the EU."],
            ["Roadmap", "", "Q1: second-generation gripper.", "Q3: multi-site planner."])),
        ("diligence", "corporate", "Реестр акционеров", "cap-table.pdf", _pdf(
            "Cap table (synthetic)", ["Founders: 70%", "Employee pool: 10%", "Angels: 20%"])),
        ("diligence", "legal", "Проект term sheet", "term-sheet.docx", files.make_docx([
            "Term sheet (draft, synthetic)", "Investment: EUR 2.5M.", "Pre-money valuation: EUR 10M (fictional).",
            "Instrument: preferred shares.", "This document contains no real terms."])),
        ("diligence", "tax", "Налоговая отчётность 2025", "tax-2025.pdf", _pdf(
            "Tax summary 2025", ["Corporate tax paid: EUR 0 (loss-making, synthetic).", "VAT filings: up to date."],
            ["Open audits: none."], ["Notes: all figures are invented for the demo."])),
        ("restricted_legal", "contracts", "Типовой договор с заказчиком", "customer-contract.pdf", _pdf(
            "Customer contract (template)", ["Parties: Nordlicht Robotics and the Customer.", "Term: 36 months.", "Service levels: 99% availability."],
            ["Liability: capped at 12 monthly fees.", "Governing law: fictional."])),
        ("restricted_legal", "disputes", "Реестр споров", "disputes.pdf", _pdf(
            "Dispute register", ["Open disputes: none.", "Closed in the last 3 years: 1 (synthetic)."])),
    ]


class Command(BaseCommand):
    help = "Fill a development instance with synthetic demo data (rooms, documents, visitors, audit). Safe to run again."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo is for development only (DJANGO_DEBUG=1).")
        admin = User.objects.filter(role=User.Role.ADMIN).order_by("id").first()
        if admin is None:
            raise CommandError("Create the first administrator first (bootstrap_admin).")
        language = InstanceSettings.current().effective_locale
        now = timezone.now()

        created_rooms = services.create_starter_rooms(language, admin)
        rooms = {r.template_key: r for r in Room.objects.exclude(template_key="")}
        folder = lambda room, key: Folder.objects.get(room=rooms[room], template_key=key)  # noqa: E731

        added = 0
        for room_key, folder_key, title, name, data in documents():
            try:
                services.create_document(folder(room_key, folder_key), title, SimpleUploadedFile(name, data), admin)
                added += 1
            except ValidationError:
                pass  # уже есть
        deck = Document.objects.filter(title="Презентация для инвесторов", folder__room=rooms["overview"]).first()
        if deck is not None and deck.versions.count() == 1:
            services.add_version(deck, SimpleUploadedFile("investor-presentation-v2.pdf", files.make_pdf(
                "Investor presentation v2", [{"lines": ["Updated figures after the Q3 close (synthetic)."]}, {"lines": ["See the Q3 report."]}])), admin)

        # NDA для комнаты due diligence
        diligence = rooms["diligence"]
        if not diligence.nda_required:
            diligence.nda_required = True
            diligence.save(update_fields=["nda_required"])
        nda = diligence.current_nda
        if nda is None:
            nda = NdaVersion.objects.create(room=diligence, number=1, created_by=admin)
            for code, body in NDA_TEXTS.items():
                NdaText.objects.create(version=nda, language=code, body=body)

        users = {}
        for email, name, lang in INVESTORS:
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(email, DEMO_PASSWORD, name=name, language=lang, email_confirmed_at=now)
            users[email] = user

        def grant(email, room_key, folder_key=None, download=False, expires=None, revoked=None):
            user = users[email]
            target = folder(room_key, folder_key) if folder_key else None
            exists = AccessGrant.objects.filter(user=user, room=rooms[room_key], folder=target).exists()
            if not exists:
                g = AccessGrant.objects.create(user=user, room=rooms[room_key], folder=target, can_download=download,
                                               expires_at=expires, revoked_at=revoked, created_by=admin)
                AuditEvent.objects.create(code=events.GRANT_CREATED, actor=admin, target=f"grant:{g.pk}")

        grant("investor1@example.com", "overview", download=True)
        grant("investor1@example.com", "diligence")
        grant("investor2@example.com", "overview", "financials", expires=now + timedelta(days=14))
        grant("investor2@example.com", "overview", "team", expires=now + timedelta(days=14))
        grant("investor3@example.com", "diligence", download=True)
        grant("investor4@example.com", "overview", expires=now - timedelta(days=1))
        grant("investor4@example.com", "restricted_legal", revoked=now - timedelta(hours=3))

        NdaAcceptance.objects.get_or_create(user=users["investor1@example.com"], version=nda, defaults={"language": "ru"})

        if not Invitation.objects.filter(email="invited@example.com").exists():
            invite_services.create_invitation(
                email="invited@example.com", name="Приглашённый инвестор", language="ru", room=rooms["overview"],
                folder=folder("overview", "summary"), can_download=False, access_expires_at=None, valid_days=7,
                created_by=admin, send_email=False)
        if not AccessRequest.objects.filter(email="karl@example.com").exists():
            request, _ = invite_services.submit_access_request(
                email="karl@example.com", name="Karl Jonsson", password=DEMO_PASSWORD, language="en",
                message="Representing a small family office. Interested in the seed round.")
            if request is not None:
                invite_services.confirm_request(request.pk)

        self._activity(admin, users, rooms)
        self._page_times(users)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready: {created_rooms} new starter rooms, {added} new documents."))
        self.stdout.write("Visitors (password for all: %s):" % DEMO_PASSWORD)
        for email, name, lang in INVESTORS:
            self.stdout.write(f"  {email}  {name} [{lang}]")
        self.stdout.write("Pending: an invitation (invited@example.com) and an access request (karl@example.com).")

    def _activity(self, admin, users, rooms):
        """Несколько прошлых действий для журнала. Один раз."""
        if AuditEvent.objects.filter(code=events.DOCUMENT_VIEWED, actor=users["investor1@example.com"]).exists():
            return
        now = timezone.now()
        docs = {d.title: d for d in Document.objects.select_related("folder")}

        def log(hours_ago, code, actor, target="", outcome=AuditEvent.Outcome.OK):
            AuditEvent.objects.create(code=code, actor=actor, target=target, outcome=outcome,
                                      created_at=now - timedelta(hours=hours_ago))

        i1, i2, i3 = users["investor1@example.com"], users["investor2@example.com"], users["investor3@example.com"]
        deck, report = docs["Презентация для инвесторов"], docs["Отчёт за 3 квартал"]
        log(70, events.NDA_ACCEPTED, i1, f"room:{rooms['diligence'].pk}:nda:1")
        for hours, user, doc in [(48, i1, deck), (47, i1, report), (30, i2, report), (26, i2, deck), (5, i1, report)]:
            log(hours, events.AUTH_SIGNED_IN, user, f"user:{user.pk}")
            log(hours, events.DOCUMENT_VIEWED, user, f"document:{doc.pk}:v1")
        log(46, events.DOCUMENT_DOWNLOADED, i1, f"document:{report.pk}:v1")
        log(29, events.DOCUMENT_DOWNLOAD_DENIED, i2, f"document:{report.pk}", AuditEvent.Outcome.DENIED)
        log(12, events.AUTH_SIGN_IN_FAILED, None, "", AuditEvent.Outcome.FAILED)
        log(2, events.AUTH_SIGNED_IN, i3, f"user:{i3.pk}")

    def _page_times(self, users):
        """Время на страницах для экрана аналитики. Один раз; только у PDF с известным числом страниц."""
        if PageTime.objects.exists():
            return
        now = timezone.now()
        plan = [
            ("investor1@example.com", "Презентация для инвесторов", [40, 25, 70, 12, 18]),
            ("investor1@example.com", "Отчёт за 3 квартал", [55, 90, 20, 35]),
            ("investor2@example.com", "Отчёт за 3 квартал", [30, 65, 8]),
            ("investor3@example.com", "Реестр акционеров", [22]),
        ]
        for email, title, seconds in plan:
            doc = Document.objects.filter(title=title).first()
            version = doc.versions.first() if doc else None
            if version is None or version.extension != "pdf":
                continue
            for page, value in enumerate(seconds[: version.page_count or 0], start=1):
                PageTime.objects.create(user=users[email], version=version, page=page, seconds=value,
                                        first_seen=now - timedelta(hours=5), last_seen=now - timedelta(hours=5))
