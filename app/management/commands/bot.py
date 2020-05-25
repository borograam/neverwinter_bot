from functools import wraps
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from telegram.ext import Updater, CommandHandler, ConversationHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.models import Guild, TelegramUser, InfluenceCollection


logger = logging.getLogger(__name__)

COLLECT = 0  # state for the conversation


class Log(object):
    def __init__(self, at_start=False, at_finish=False, level='INFO'):
        self.at_start = at_start
        self.at_finish = at_finish
        self.level = level

    def __call__(self, func):
        @wraps(func)
        def wrapper(update, context):
            if self.at_start:
                self.log('start', update)
            result = func(update, context, self.get_text_replier(update))
            if self.at_finish:
                self.log('finish', update)
            return result

        self.func_name = func.__name__
        return wrapper

    def _get_format_kwargs(self, update):
        return {
            'name': self.func_name,
            'chat': update.effective_chat.id,
            'sender': update.effective_message.from_user.id
        }

    def log(self, action, update):
        log_message = "{action} '{name}' function in {chat} chat. Sender id: {sender}".format(
            action=action,
            **self._get_format_kwargs(update)
        )
        logger.log(logging.getLevelName(self.level), log_message)

    def get_text_replier(self, update):
        @wraps(update.message.reply_text)
        def reply_text(*args, **kwargs):
            log_message = "inside '{name}': try to reply in {chat} chat to message from {sender}".format(
                **self._get_format_kwargs(update)
            )
            logger.log(logging.getLevelName(self.level), log_message)
            return update.message.reply_text(*args, **kwargs)

        return reply_text


class Command(BaseCommand):

    def handle(self, *args, **options):
        updater = Updater(token=settings.TELEGRAM_TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        @Log(at_start=True, at_finish=True)
        def start(update, context, reply_text):
            reply_text("Здравствуйте! Нажмите на /help , чтобы узнать как пользоваться ботом.")

        @Log(at_start=True, at_finish=True)
        def collect(update, context, reply_text):
            chat = update.effective_chat
            if chat.type in [chat.GROUP, chat.SUPERGROUP]:
                try:
                    guild = Guild.objects.get(chat_id=chat.id)
                except Guild.DoesNotExist as e:
                    reply_text('Эта группа не зарегистрирован как чат определённой гильдии. Используйте '
                               'тут команду `/register ИМЯ ГИЛЬДИИ` для регистрации')
                    return ConversationHandler.END
                tuser, created = TelegramUser.get_or_create_by_api(update.effective_message.from_user)
                if created:
                    logger.info('created TelegramUser object pk {}'.format(tuser.pk))
                added = guild.make_sure_user_is_member(tuser)
                if added:
                    logger.info('add TelegramUser pk {} as member of Guild pk {}'.format(tuser.pk, guild.pk))
                c = InfluenceCollection.create(tuser, guild)
                logger.info('create InfluenceCollection pk {}'.format(c.pk))
                reply_text('Принято. Отсчёт пошёл.')
                return ConversationHandler.END

            elif chat.type == chat.PRIVATE:
                try:
                    tuser = TelegramUser.objects.get(chat_id=chat.id)
                except TelegramUser.DoesNotExist as e:
                    message = 'Я пока что не знаю кто Вы и к какой гильдии относитесь. В первый раз напишите, ' \
                              'пожалуйста, обращение ко мне через свою группу.'
                    reply_text(message)
                    return ConversationHandler.END
                keyboard = [[guild.name] for guild in tuser.guilds.all()]
                keyboard += [['--Другая--']]
                reply_text('Выберите гильдию:', reply_markup=ReplyKeyboardMarkup(keyboard,
                                                                                 one_time_keyboard=True,
                                                                                 resize_keyboard=True))
                return COLLECT

        @Log(at_start=True, at_finish=True)
        def guild_choice(update, context, reply_text):
            # conversation guarantied user exists
            tuser = TelegramUser.objects.get(chat_id=update.effective_chat.id)
            try:
                guild = Guild.objects.get(name=update.message.text, members=tuser)
            except Guild.DoesNotExist as e:
                reply_text('Напишите обращение ко мне из группы гильдии, чтобы я запомнил, что Вы в ней '
                           'состоите', reply_markup=ReplyKeyboardRemove())
                return ConversationHandler.END

            c = InfluenceCollection.create(tuser, guild)
            logger.info('create InfluenceCollection pk {}'.format(c.pk))
            reply_text('Принято. Отсчёт пошёл.', reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        @Log(at_start=True, at_finish=True)
        def cancel(update, context, reply_text):
            reply_text('Разговор отменён.'.format(update.message.text),
                       reply_markup=ReplyKeyboardRemove())

        @Log(at_start=True, at_finish=True)
        def register(update, context, reply_text):
            chat = update.effective_chat
            name = " ".join(context.args)
            if chat.type in [chat.GROUP, chat.SUPERGROUP]:
                try:
                    guild = Guild.objects.get(chat_id=chat.id)
                    message = 'Данный чат уже зарегистрирован на гильдию "{}". Потом сделаем возможность ' \
                              'переименовывания, пока только вручную.. обращаться к @borograam'
                    reply_text(message.format(guild.name))
                    return
                except Guild.DoesNotExist as e:
                    pass
                if name == "":
                    reply_text('Вы забыли указать имя гильдии. Напишите в окне ввода (руками) /register и допишите имя '
                               'Вашей гильдии')
                    return
                guild = Guild.objects.create(chat_id=chat.id, name=name)
                logger.info('create Guild pk {}'.format(guild.pk))

                api_user = update.effective_message.from_user
                tuser, created = TelegramUser.get_or_create_by_api(api_user)
                if created:
                    logger.info('create TelegramUser pk {}'.format(tuser.pk))
                added = guild.make_sure_user_is_member(tuser)
                if added:
                    logger.info('added TelegramUser pk {} as member to Guild pk {}'.format(tuser.pk, guild.pk))

                reply_text('Гильдия {} зарегистрирована'.format(name))
            else:
                reply_text('Разрешается регистрировать лишь группы.')

        @Log(at_start=True, at_finish=True)
        def help_command(update, context, reply_text):
            private = """
            Для использования бота должны выполняться некоторые условия:
            - необходимо быть участником группы, что зарегистрирована ботом как гильдия
            - хотя бы один раз сообщить о сборе ресурсов (/collect) в общем чате гильдии
            Это позволит системе запомнить, что Вы являетесь членом гильдии (боту недоступен список участников, а так \
            же любые сообщения, что не обращены к нему).
            Основная команда: /collect . Использование в общем чате или в личной переписке с ботом равноценно: ставится\
            таймер на 8 часов. Если за прошедшее время никто ни разу не собрал ресурсы, о переполнении будет сообщено в\
            общую группу гильдии.
            Регистрация группы как "чата гильдии" происходит лишь один раз (при первом /collect об этом будет сообщен
            """
            group = """
            Основная команда: /collect . Каждый человек, использовав команду один раз тут, сможет писать её боту личным 
            сообщением. 
            При использовании команды ставится таймер на 8 часов. Если за прошедшее время никто ни разу не собрал 
            ресурсы, о переполнении будет сообщено сюда.
            Регистрация группы как Эчата гильдииЭ происходит лишь один раз (при первом /collect об этом будет сообщено)
            """
            chat = update.effective_chat
            if chat.type == chat.PRIVATE:
                reply_text(private)
            else:
                reply_text(group)

        @Log(at_start=True, level="ERROR")
        def error(update, context, reply_text):
            logger.error("context has this error: {}".format(context.error))
            print('Update "{}" caused error "{}"'.format(update, context.error))

        dispatcher.add_handler(CommandHandler('start', start))
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('collect', collect)],
            states={
                COLLECT: [MessageHandler(Filters.text, guild_choice)]
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(CommandHandler('register', register, pass_args=True))
        dispatcher.add_handler(CommandHandler('help', help_command))

        dispatcher.add_error_handler(error)

        print('starting the bot... Ctrl-C to exit')
        logger.info("start bot polling")
        updater.start_polling()
        updater.idle()
        logger.info("stop bot polling")


if __name__ == '__main__':
    command = Command()
