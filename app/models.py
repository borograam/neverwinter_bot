import logging
import re
import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericRelation, GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from redis import Redis
from rq_scheduler import Scheduler
from telegram import ParseMode
from telegram.ext import Updater

from app.tasks import notification_job

logger = logging.getLogger(__name__)


class TelegramUser(models.Model):
    django = models.OneToOneField(User, on_delete=models.CASCADE, related_name='telegram')
    first_name = models.CharField(max_length=30, null=True)
    last_name = models.CharField(max_length=150, null=True)
    link = models.CharField(max_length=100, null=True)
    name = models.CharField(max_length=50, default='')

    chat_id = models.CharField(max_length=20)

    @classmethod
    def get_or_create_by_api(cls, api_user):
        created = False
        try:
            obj = cls.objects.get(chat_id=api_user.id)
        except cls.DoesNotExist:
            user = User.objects.create(username=uuid.uuid4())
            obj = cls.objects.create(django=user, chat_id=api_user.id)
            created = True

        obj.first_name = api_user.first_name
        obj.last_name = api_user.last_name
        obj.link = api_user.link
        obj.name = api_user.name
        obj.save()

        return obj, created

    def get_display_name_for_guild(self, guild):
        """
        :param guild: Guild object
        :return: string - display_name from GuildMembership or self.name
        throws GuildMembership.DoesNotExist if user is not member of guild
        """
        name = GuildMembership.objects.get(tuser=self, guild=guild).display_name
        return name or self.name

    def set_display_name_for_guild(self, name, guild):
        obj = GuildMembership.objects.get(tuser=self, guild=guild)
        obj.display_name = name
        obj.save()

    def mention_html_for_guild(self, guild):
        from telegram.utils.helpers import mention_html
        return mention_html(int(self.chat_id), self.get_display_name_for_guild(guild))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create TelegramUser pk {}'.format(self.pk))

    def __str__(self):
        return self.name


class Guild(models.Model):
    members = models.ManyToManyField(TelegramUser, related_name='guilds', through='GuildMembership')
    name = models.CharField(max_length=50)
    chat_id = models.CharField(max_length=20)
    additional_notifications = models.CharField(max_length=100, default='')

    def make_sure_user_is_member(self, tuser):
        added = False
        try:
            self.members.get(pk=tuser.pk)
        except TelegramUser.DoesNotExist:
            GuildMembership.objects.create(tuser=tuser, guild=self)
            logger.info('add TelegramUser pk {} as member to Guild pk {}', tuser.pk, self.pk)
            added = True
        return added

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create Guild pk {}'.format(self.pk))

    def __str__(self):
        return self.name


class GuildMembership(models.Model):
    tuser = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    guild = models.ForeignKey(Guild, on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now=True)
    display_name = models.CharField(max_length=16, blank=True)
    # TODO: history of name changes ?


class Notification(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()

    caused_by = GenericForeignKey()
    number = models.PositiveIntegerField(default=0)
    time = models.DateTimeField()
    job_id = models.CharField(max_length=36)
    canceled = models.BooleanField(default=False)
    notified = models.BooleanField(default=False)

    @classmethod
    def filter_by_reason(cls, obj_or_model):
        from django.db.models.base import ModelBase
        model = obj_or_model
        if type(model) != ModelBase:
            model = obj_or_model.__class__
        return cls.objects.filter(content_type=ContentType.objects.get_for_model(model))

    @classmethod
    def create(cls, reason, at_time, number=None):
        """reason must me an object of some model which implements ActionMixin"""
        content_type = ContentType.objects.get_for_model(reason.__class__)
        if number is None:
            last_not = cls.objects.filter(content_type=content_type, object_id=reason.id).last()
            number = last_not.number + 1 if last_not is not None else 0
        else:
            if cls.objects.filter(content_type=content_type, object_id=reason.id, number__gte=number).exists():
                return None  # exception?
        #cls.objects.filter(content_type=content_type, object_id=)
        obj = cls.objects.create(time=at_time, caused_by=reason, number=number)
        scheduler = Scheduler(connection=Redis())
        job = scheduler.enqueue_at(at_time, notification_job, obj.pk)
        logger.info('Notification pk {}: enqueue job to scheduler'.format(obj.pk))
        obj.job_id = job.id
        obj.save()

        return obj

    def cancel(self):
        logger.info('cancelling future Notification pk {}'.format(self.pk))
        scheduler = Scheduler(connection=Redis())
        scheduler.cancel(self.job_id)
        self.canceled = True
        self.save()

    def __str__(self):
        return "{} - {}".format(self.caused_by, self.number)


class ActionMixin(models.Model):
    class Meta:
        abstract = True
    by = models.ForeignKey(TelegramUser, on_delete=models.PROTECT)
    at = models.DateTimeField()
    in_guild = models.ForeignKey(Guild, on_delete=models.PROTECT)
    # notifications = GenericRelation(Notification, related_query_name='caused_by')

    def notify(self, notification):
        """this method will be called from rq queue"""
        raise NotImplementedError

    def get_next_notification_delta(self, last_notification):
        return None


class ResourceCollection(ActionMixin):
    notifications = GenericRelation(Notification, related_query_name='resource_collection')

    @classmethod
    def create(cls, tuser, guild, time=None):
        if time is None:
            time = timezone.now()
        obj = cls.objects.create(by=tuser, at=time, in_guild=guild)

        for notification in Notification.objects.filter(
                canceled=False,
                notified=False,
                resource_collection__in_guild=guild):
            notification.cancel()

        Notification.create(obj, time + timezone.timedelta(seconds=30))  # TODO: change interval to 8 hours
        return obj

    def notify(self, notification):
        updater = Updater(token=settings.TELEGRAM_TOKEN, use_context=True)
        # TODO: make this message customization
        if notification.number == 0:
            message = "Согласно моим данным, ресурсы переполнились."
        else:
            message = "Повторяю: ресурсы переполнились и никто их не хочет собирать!"
        updater.bot.send_message(self.in_guild.chat_id, message)
        logger.info("  message sent to chat {}, which stored in Guild pk {}".format(
            self.in_guild.chat_id,
            self.in_guild.pk)
        )

    def get_next_notification_delta(self, last_notification):
        def generator():
            string = self.in_guild.additional_notifications
            regexp = re.compile(r'\+(\d+)([mh])(?:\[(\d+|\*)\])?')
            for t in regexp.findall(string):
                minutes = int(t[0]) * (60 if t[1] == 'h' else 1)
                repeat = t[2]
                if repeat == '*':
                    while True:
                        yield minutes
                repeat = int(repeat) if repeat != '' else 1
                for _ in range(repeat):
                    yield minutes

        skip = last_notification.number
        for minutes in generator():
            if skip > 0:
                skip -= 1
                continue
            return timezone.timedelta(minutes=minutes)
        return None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create ResourceCollection object pk {}'.format(self.pk))

    def __str__(self):
        return "{} - {}".format(self.in_guild.name, self.at.astimezone().strftime("%d.%m.%y %H:%M"))


class TemporaryNPC(ActionMixin):
    notifications = GenericRelation(Notification, related_query_name='temporary_npc')
    caption = models.CharField(max_length=50)
    expired = models.BooleanField(default=False)

    @classmethod
    def create(cls, caption=None, by=None, in_guild=None, ended_at=None):
        if not caption or not by or not in_guild or not ended_at:
            raise ValueError('caption, by, in_guild and ended_at are mandatory to create TemporaryNPC')
        obj = cls.objects.create(caption=caption, by=by, in_guild=in_guild, at=ended_at)
        remaining = ended_at - timezone.now()
        if remaining < timezone.timedelta(minutes=30):
            Notification.create(obj, ended_at, number=3)
        elif remaining < timezone.timedelta(hours=1):
            Notification.create(obj, ended_at - timezone.timedelta(minutes=30), number=2)
        elif remaining < timezone.timedelta(days=1):
            Notification.create(obj, ended_at - timezone.timedelta(hours=1), number=1)
        else:
            Notification.create(obj, ended_at - timezone.timedelta(days=1))
        return obj

    def notify(self, notification):
        updater = Updater(token=settings.TELEGRAM_TOKEN, use_context=True)
        if notification.number == 0:
            text = f'По моим данным, <b>{self.caption}</b> уходит через сутки. Теперь игра показывает не только ' \
                   f'оставшиеся часы, но ещё и минуты. Сверьте их, пожалуйста, для более точного уведомления об ' \
                   f'окончании.\nКто-угодно, находясь в игре, вызовите у бота команду /get_npc_list'
        elif notification.number == 1:
            text = f'Через час закончится время, когда <b>{self.caption}</b> находится в крепости.'
        elif notification.number == 2:
            text = f'Осталось лишь 15 минут! <b>{self.caption}</b> уже написал завещание!'
        else:
            text = f'<b>{self.caption}</b> ушёл из крепости.'
            self.expired = True
            self.save()
        updater.bot.send_message(self.in_guild.chat_id, text, parse_mode=ParseMode.HTML)

    def get_next_notification_delta(self, last_notification):
        if last_notification.number == 0:
            return timezone.timedelta(hours=23)
        elif last_notification.number == 1:
            return timezone.timedelta(minutes=45)
        elif last_notification.number == 2:
            return timezone.timedelta(minutes=15)
        return None

    def __str__(self):
        return f'({self.pk}) {self.caption} - {self.in_guild.name}'
