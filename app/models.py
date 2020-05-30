import logging
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from redis import Redis
from rq_scheduler import Scheduler

from app.tasks import notification

logger = logging.getLogger(__name__)


class TelegramUser(models.Model):
    django = models.OneToOneField(User, on_delete=models.CASCADE, related_name='telegram')
    first_name = models.CharField(max_length=30, default='')
    last_name = models.CharField(max_length=150, default='')
    link = models.CharField(max_length=100, default='')
    name = models.CharField(max_length=50, default='')

    chat_id = models.CharField(max_length=20)

    @classmethod
    def get_or_create_by_api(cls, api_user):
        created = False
        try:
            obj = cls.objects.get(chat_id=api_user.id)
        except cls.DoesNotExist:
            #user = User.objects.create(first_name=api_user.first_name,
            #                           last_name=api_user.last_name,
            #                           username=uuid.uuid4())
            user = User.objects.create(username=uuid.uuid4())
            obj = cls.objects.create(django=user, chat_id=api_user.id)
            created = True

        # obj.first_name = api_user.first_name
        # obj.last_name = api_user.last_name
        # obj.link = api_user.link
        obj.name = api_user.name
        obj.save()

        return obj, created

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create TelegramUser pk {}'.format(self.pk))

    def __str__(self):
        return self.name


class Guild(models.Model):
    members = models.ManyToManyField(TelegramUser, related_name='guilds')
    name = models.CharField(max_length=50)
    chat_id = models.CharField(max_length=20)

    def make_sure_user_is_member(self, tuser):
        added = False
        try:
            self.members.get(pk=tuser.pk)
        except TelegramUser.DoesNotExist as e:
            self.members.add(tuser)
            logger.info('add TelegramUser pk {} as member to Guild pk {}', tuser.pk, self.pk)
            added = True
        return added

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create Guild pk {}'.format(self.pk))

    def __str__(self):
        return self.name


class InfluenceCollection(models.Model):
    by = models.ForeignKey(TelegramUser, on_delete=models.PROTECT)
    at = models.DateTimeField()
    in_guild = models.ForeignKey(Guild, on_delete=models.PROTECT)
    notification_job_id = models.CharField(max_length=30)
    waiting_to_notify = models.BooleanField(default=True)
    notified = models.BooleanField(default=False)

    @classmethod
    def create(cls, tuser, guild, time=None):
        if time is None:
            time = timezone.now()
        obj = cls.objects.create(by=tuser, at=time, in_guild=guild)

        for col in cls.objects.filter(in_guild=guild, waiting_to_notify=True):
            col.cancel_notification()

        scheduler = Scheduler(connection=Redis())
        # TODO: change time delta to 8 hours
        job = scheduler.enqueue_in(timezone.timedelta(hours=8), notification, obj.pk)
        logger.info('InfluenceCollection pk {}: enqueue job to scheduler'.format(obj.pk))
        obj.notification_job_id = job.id
        obj.save()

        return obj

    def cancel_notification(self):
        logger.info('cancelling future notification in InfluenceCollection pk {}'.format(self.pk))
        scheduler = Scheduler(connection=Redis())
        scheduler.cancel(self.notification_job_id)
        self.waiting_to_notify = False
        self.save()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info('create InfluenceCollection object pk {}'.format(self.pk))

    def __str__(self):
        return "{} - {}".format(self.in_guild.name, self.at.astimezone().strftime("%d.%m.%y %H:%M"))
