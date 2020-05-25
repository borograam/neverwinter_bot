import logging

from django.conf import settings
from telegram.ext import Updater
from django.apps import apps


get_model = apps.get_model
logger = logging.getLogger(__name__)


def notification(collection_pk):
    logger.info('start notification job. InfluenceCollection pk {}'.format(collection_pk))
    InfluenceCollection = get_model('app', 'InfluenceCollection')

    collection = InfluenceCollection.objects.get(pk=collection_pk)
    if collection.notified or not collection.waiting_to_notify:
        logger.warning("  InfluenceCollection pk {} marked as not waiting or already notified".format(collection_pk))
        return False
    updater = Updater(token=settings.TELEGRAM_TOKEN, use_context=True)
    updater.bot.send_message(collection.in_guild.chat_id, "Согласно моим данным, влияние переполнилось.")
    logger.info("  message sent to chat {}, which stored in Guild pk {}".format(
        collection.in_guild.chat_id,
        collection.in_guild.pk)
    )
    collection.waiting_to_notify = False
    collection.notified = True
    collection.save()
    logger.info('stop notification job. InfluenceCollection pk {}'.format(collection_pk))
    return True
