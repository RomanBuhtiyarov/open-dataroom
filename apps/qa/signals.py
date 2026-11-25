"""При удалении папки вопросы, привязанные к ней, не должны стать видимыми всей комнате."""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.rooms.models import Folder

from .models import Question


@receiver(pre_delete, sender=Folder)
def keep_questions_in_scope(sender, instance, **kwargs):
    """Папка без вопросов-«сирот»: вопрос переходит в родительскую папку (или в комнату) и перестаёт быть общим.

    Без этого внешний ключ стал бы пустым, а пустая область означает «вся комната»: ответ, которым администратор
    поделился для одной папки, увидели бы все посетители комнаты. Автор по-прежнему видит свой вопрос."""
    Question.objects.filter(folder_id=instance.pk).update(folder_id=instance.parent_id, shared=False)
