import logging

from django.apps import apps


get_model = apps.get_model
logger = logging.getLogger(__name__)


def notification_job(notification_pk):
    logger.info('start notification job. Notification pk {}'.format(notification_pk))
    Notification = get_model('app', 'Notification')

    n = Notification.objects.get(pk=notification_pk)
    if n.notified or n.canceled:
        logger.warning("  Notification pk {} marked as canceled or already notified".format(notification_pk))
        return False

    n.caused_by.notify(n)
    n.notified = True
    n.save()

    delta = n.caused_by.get_next_notification_delta(n)
    if delta is not None:
        n2 = Notification.create(n.caused_by, n.time + delta)
        logger.info('  create new notification pk {}'.format(n2.pk))
    logger.info('stop notification job. Notification pk {}'.format(notification_pk))
    return True
