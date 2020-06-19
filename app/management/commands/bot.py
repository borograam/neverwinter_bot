import re
import sys
import traceback
from functools import wraps
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from telegram.error import ChatMigrated
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
from telegram import ReplyKeyboardRemove, ParseMode, InlineKeyboardButton, InlineKeyboardMarkup, Chat, ChatMember, \
    Update, constants
from telegram.utils.helpers import mention_html, escape_markdown

from app.models import Guild, TelegramUser, ResourceCollection, TemporaryNPC

logger = logging.getLogger(__name__)

# TODO: ? python argparse for telegram commands ?


class Log(object):
    def __init__(self, at_start=False, at_finish=False, level='INFO'):
        self.at_start = at_start
        self.at_finish = at_finish
        self.level = level

    def __call__(self, func):
        @wraps(func)
        def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
            if self.at_start:
                self.log('start', update)
            if not kwargs.get('reply'):
                kwargs['reply'] = self.get_text_replier(update)
            result = func(update, context, *args, **kwargs)
            if self.at_finish:
                self.log('finish', update)
            return result

        self.func_name = func.__name__
        return wrapper

    def _get_format_kwargs(self, update: Update):
        return {
            'name': self.func_name,
            'chat': update.effective_chat.id,
            'sender': update.effective_message.from_user.id
        }

    def log(self, action, update: Update):
        log_message = "{action} '{name}' function in {chat} chat. Sender id: {sender}".format(
            action=action,
            **self._get_format_kwargs(update)
        )
        logger.log(logging.getLevelName(self.level), log_message)

    def get_text_replier(self, update: Update):
        @wraps(update.effective_message.reply_text)
        def reply_text(*args, **kwargs):
            log_message = "inside '{name}': try to reply in {chat} chat to message from {sender}".format(
                **self._get_format_kwargs(update)
            )
            logger.log(logging.getLevelName(self.level), log_message)
            # logger.info(f'mes: {update.effective_message}, args: {args}, kwargs: {kwargs}')
            return update.effective_message.reply_text(*args, **kwargs)

        return reply_text


def private_guild_choice(f):
    """pass `guild` arg in `f` if chat is private and `tuser` arg"""
    try:
        mem = private_guild_choice.mem
    except AttributeError:
        mem = private_guild_choice.mem = {}

    @wraps(f)
    @Log(at_start=True, at_finish=True)
    def guild_choice_wrapper(update: Update, context: CallbackContext, *args, reply=None, **kwargs):
        chat = update.effective_chat
        if chat.type == chat.PRIVATE:
            try:
                tuser = TelegramUser.objects.get(chat_id=chat.id)
            except TelegramUser.DoesNotExist as e:
                message = 'Я пока что не знаю кто Вы и к какой гильдии относитесь. В первый раз напишите, ' \
                          'пожалуйста, обращение ко мне через свою группу.'
                reply(message)
                return

            def other():
                pass
            other.name = '-Другая-'
            other.chat_id = "other"
            guilds = list(tuser.guilds.all()) + [other]
            keyboard = [
                [InlineKeyboardButton(g.name, callback_data=str(g.chat_id)) for g in row]
                for row in
                [guilds[i:i+3] for i in range(0, len(guilds), 3)]
            ]
            m = reply('Выберите гильдию:', reply_markup=InlineKeyboardMarkup(keyboard))
            mem[(m.chat.id, m.message_id)] = (f, context)
            return
        logger.info(f'start {f.__name__} function in wrapper')
        ret = f(update, context, *args, reply=reply, **kwargs)
        logger.info(f'finish {f.__name__} function in wrapper')
        return ret

    return guild_choice_wrapper


@Log(at_start=True, at_finish=True)
def guild_callback(update: Update, context: CallbackContext, *args, reply=None, **kwargs):
    query = update.callback_query
    query.answer()

    def get_edit_message_text_for_guild(g):
        @wraps(query.edit_message_text)
        def edit_message_text(text, *args2, **kwargs2):
            parse_mode = kwargs2.get('parse_mode')
            if not parse_mode or parse_mode == ParseMode.HTML:
                kwargs2['parse_mode'] = ParseMode.HTML
                text = f'<i>({g.name})</i> {text}'
            elif parse_mode == ParseMode.MARKDOWN_V2:

                text = f'_\\({escape_markdown(g.name, 2)}\\)_ {text}'
            elif parse_mode == ParseMode.MARKDOWN:
                text = f'_({escape_markdown(g.name, 1)})_ {text}'
            logger.info(f'try to edit message')
            return query.edit_message_text(text, *args2, **kwargs2)  # text=f"{query.data}"
        return edit_message_text

    if query.data == "other":
        query.edit_message_text('Напишите обращение ко мне из группы гильдии, чтобы я запомнил, что Вы в ней состоите')
        return

    m = update.effective_message
    key_in_mem = (m.chat.id, m.message_id)
    f, old_context = private_guild_choice.mem.get(key_in_mem, (None, None))
    if f is not None:
        guild = Guild.objects.get(chat_id=update.callback_query.data)  # need to check?
        tuser, _ = TelegramUser.get_or_create_by_api(update.effective_user)
        logger.info(f'start {f.__name__} function in callback')
        ret = f(update, old_context, reply=get_edit_message_text_for_guild(guild), guild=guild, tuser=tuser)
        del private_guild_choice.mem[key_in_mem]
        logger.info(f'finish {f.__name__} function in callback')
        return ret
    logger.info(f'can\'t remember {key_in_mem} message to call it in callback')  # may be use another level?


def chat_types_only(type_list, message, *args, **kwargs):
    def decorator(f):
        @wraps(f)
        @Log(at_start=True, at_finish=True)
        def wrapper(update: Update, context: CallbackContext, *args2, reply=None, **kwargs2):
            chat = update.effective_chat
            if chat.type not in type_list:
                #logger.info(message + f', args: {args}, kwargs: {kwargs}')
                reply(message, *args, **kwargs)
                return
            logger.info('call wrapped function')
            return f(update, context, *args2, **kwargs2, reply=reply)
        return wrapper
    return decorator


def groups_only(message, *args, **kwargs):
    return chat_types_only([Chat.GROUP, Chat.SUPERGROUP], message, *args, **kwargs)


def privates_only(message, *args, **kwargs):
    return chat_types_only([Chat.PRIVATE], message, *args, **kwargs)


def group_registered(f):
    """find guild and pass to `guild` arg. Find or create telegram user and pass to `tuser` arg"""
    @wraps(f)
    @Log(at_start=True, at_finish=True)
    def wrapper(update: Update, context: CallbackContext, *args, reply=None, **kwargs):
        chat = update.effective_chat
        if chat.type in [chat.GROUP, chat.SUPERGROUP]:
            try:
                guild = Guild.objects.get(chat_id=chat.id)
                kwargs['guild'] = guild

                tuser, created = TelegramUser.get_or_create_by_api(update.effective_message.from_user)
                kwargs['tuser'] = tuser
                if created:
                    logger.info(f'created TelegramUser object pk {tuser.pk}')
                added = guild.make_sure_user_is_member(tuser)
                if added:
                    logger.info(f'add TelegramUser pk {tuser.pk} as member of Guild pk {guild.pk}')
            except Guild.DoesNotExist as e:
                reply('Эта группа не зарегистрирована как чат определённой гильдии\\. Используйте '
                      'тут команду `/register ИМЯ ГИЛЬДИИ` для регистрации\n\nЕсли же Вы уверены, что группа ранее '
                      'регистрировалась, есть вероятность того, что она мигрировала в "супергруппу" в период '
                      'неактивности бота\\. Для автоматической починки в такой ситуации, сообщите о сборе ресурсов '
                      '\\(/collect\\) в личной переписке с ботом, выбрав эту самую гильдию\\.',
                      parse_mode=ParseMode.MARKDOWN_V2)
                return
        logger.info('call wrapped function')
        return f(update, context, *args, **kwargs, reply=reply)
    return wrapper


class Command(BaseCommand):

    def handle(self, *args, **options):
        updater = Updater(token=settings.TELEGRAM_TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        @Log(at_start=True, at_finish=True)
        def start(update: Update, context: CallbackContext, reply=None):
            reply("Здравствуйте! Нажмите на /help , чтобы узнать как пользоваться ботом.",
                  reply_markup=ReplyKeyboardRemove())

        @group_registered
        @private_guild_choice
        def collect(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            c = ResourceCollection.create(tuser, guild)
            logger.info(f'create ResourceCollection pk {c.pk}')
            reply('Принято. Отсчёт пошёл.', disable_notification=True)

            if update.effective_chat.type == Chat.PRIVATE:
                text = f"Пользователь {tuser.mention_html_for_guild(guild)} собрал ресурсы."
                try:
                    context.bot.send_message(guild.chat_id,
                                             text,
                                             disable_notification=True,
                                             parse_mode=ParseMode.HTML)
                except ChatMigrated as e:
                    new_id = e.new_chat_id
                    context.bot.send_message(new_id,
                                             text,
                                             disable_notification=True,
                                             parse_mode=ParseMode.HTML)
                    logger.info(f'edit chat id in guild pk {guild.pk} which migrated from {guild.chat_id} to {new_id}')
                    guild.chat_id = new_id
                    guild.save()

        @groups_only('Разрешается регистрировать лишь группы. Добавьте бота как участника группы и вызовите после '
                     'этого там команду.')
        def register(update: Update, context: CallbackContext, reply=None):
            chat = update.effective_chat
            name = " ".join(context.args)
            try:
                guild = Guild.objects.get(chat_id=chat.id)
                message = f'Данный чат уже зарегистрирован на гильдию "{guild.name}". Потом сделаем возможность ' \
                          f'переименовывания, пока только вручную.. обращаться к @borograam'
                reply(message)
                return
            except Guild.DoesNotExist as e:
                pass
            if name == "":
                reply('Вы забыли указать имя гильдии\\. Отредактируйте последнее сообщение, дописав \\(через пробел\\)'
                      ' имя гильдии\\.', parse_mode=ParseMode.MARKDOWN_V2)
                return
            guild = Guild.objects.create(chat_id=chat.id, name=name)
            logger.info('create Guild pk {}'.format(guild.pk))

            api_user = update.effective_message.from_user
            tuser, created = TelegramUser.get_or_create_by_api(api_user)
            if created:
                logger.info('create TelegramUser pk {}'.format(tuser.pk))
            guild.make_sure_user_is_member(tuser)
            logger.info('added TelegramUser pk {} as member to Guild pk {}'.format(tuser.pk, guild.pk))

            reply(f'Гильдия *{name}* зарегистрирована\\.', parse_mode=ParseMode.MARKDOWN_V2)

        @Log(at_start=True, at_finish=True)
        def help_command(update: Update, context: CallbackContext, reply=None):
            blocks = [
                """\
Функционал бота можно описать отдельными блоками. Далее по каждому из них последует некоторая информация, и список \
существующих в блоке, команд. Для понимания и удобства, каждая команда может быть помечена специальными знаками, \
которые значат:
- (рег) использование такой команды в группе "регистрирует" Вас как участника гильдии. После этого Вы можете \
пользоваться некоторым функционалом в личной переписке с ботом
- (гр) использование такой команды разрешено только в группе (иначе будет уведомлено о невозможности совершить такую \
команду)
- (лс) использование такой команды разрешено только в личном чате
- (гр|лс) использование такок команды разрешено как в группе, так и в личном чате""",

                """\
<b>1. Регистрация</b>
Вся функциональность завязана на понятии "гильдия", которая привязана к (супер)группе в телеграме. Все настройки и \
уведомления будут там.
Для регистрации необходимо добавить бота участником группы, после чего вызвать команду <code>/register</code>, указав \
имя гильдии (в дальнейшем будет использоваться для уточнения к какой именно гильдии применять действия каждому \
отдельному юзеру, ибо каждый игрок может быть участником неограниченного количества гильдий).
Основной функционал разрешено использовать как в чате гильдии, так и в личной переписке с ботом (чтобы, например, \
лишний раз не спамить в группе), но для возможности выполнений действий в "личке" Вы должны <i>хотя бы раз</i> \
использовать какую-нибудь команду, помеченную после как (рег) в чате соответствующей гильдии.
Регистрация необходима лишь один раз для каждой группы.
- <i>(гр)</i> /register <code>Имя гильдии</code> - регистрация новой группы как гильдии""",
            
                """\
<b>2. Сбор ресурсов</b>
Время переполнения любого ресурсодобывающего предприятия - 8 часов вне зависимости от уровня здания. Для \
своевременного уведомления о переполнении ресурсов необходимо, чтобы каждый раз при сборе кто-угодно фиксировал \
это у бота. Делается это командой <code>/collect</code> без каких-либо аргументов, ибо информации о времени получения \
сообщения (и ранее полученных сообщений) хватает для всех расчётов.
Каждая гильдия может согласовать между собой и договориться о графике дополнительных уведомлений (в случае \
бездействия при переполненных складах). Разрешено устанавливать неограниченное количество доп.уведомлений. Пример \
составления графика: <code>/set_additional_notifications +15m[2] +30m +1h[*]</code>, что значит "отсрочить (дважды) \
доп.уведомление о переполнении по 15 минут, после напомнить через пол часа, и продолжать бесконечно напоминать каждый \
час" (конечно же, пока что-нибудь, наконец, не соберёт ресурсы). Пример: при переполнении ресурсов в 13:24 последующие \
уведомления поступят в (+15m) 13:39, (+15m) 13:54, (+30m) 14:24, (+1h) 15:24, (+1h) 16:24 и т.д. Команда по установке \
доп.уведомлений специально разрешена только в группе, чтобы всем было известно когда и как был установлен график.
В любой момент кто-угодно может узнать текущий график с помощью команды <code>/get_additional_notifications</code> \
без аргументов.
- <i>(рег)</i> <i>(гр|лс)</i> /collect - сообщить о сборе ресурсов
- <i>(рег)</i> <i>(гр)</i> /set_additional_notifications <code>график</code> - установка нового графика доп.уведомлений
- <i>(рег)</i> <i>(гр|лс)</i> /get_additional_notifications - выдать текущий график доп.уведомлений""",
            
                """\
<b>3. Автоматическая установка никнейма в титул в группе гильдии</b>
В группах телеграма админам можно устанавливать кастомные "титулы" через управление группой. При достаточном \
количестве прав, бот может автоматически устанавливать участникам эти титулы в указанные ими ники. Так можно \
добиться того, чтобы люди в чате узнавали друг друга (вне зависимости от установленных в телеграме имён и фотографий).
Что требуется: установить боту права <u>Change group info</u> и <u>Add new admins</u>. А теперь коротко о том почему: \
Титул может устанавливаться лишь админам группы (то есть каждый участник с ником должен быть админом). Админ должен \
обязательно быть наделён одним из шести прав. В данном случае все "выдвинутые" ботом админы имеют лишь право <u>Change \
group info</u> (что самое меньшее зло, среди всех вариантов). Однако по правилам telegram, умеющий создавать админов \
админ не может наделить первого правами бОльшими, чем имеет сам. Потому бот сам должен владеть правом <u>Change group \
info</u>, чтобы выдавать его другим. Сам он даже не будет пытаться что-либо изменить.
Каждый юзер может задать свой ник для каждой гильдии, в которой является участником. Дабы не засорять чат гильдии \
такими попытками, эту команду разрешено использовать только в личной переписке с ботом.
- <i>(лс)</i> /set_display_name - установить ник в гильдию (будет предложен выбор)""",
            
                """\
<b>4. Уведомление об окончании временных построек</b>
Для того, чтобы бот заранее уведомлял о заканчивающихся временных подстройках, ему необходимо заранее сказать о том, \
какие постройки есть и какое количество времени игра показывает им осталось "жить". Делается это посредством \
команды <code>/new_npc ...</code>. Не буду подробнее описывать формат команды, вызывайте без аргументов и сообщение \
об ошибке расскажет как именно правильно составлять аргументы.
За счёт того, что при оставшемся времени больше суток игра не уточняет количество "минут" (лишь дни и часы), \
итоговое уведомление за счёт округления может отзвенеть несвоевременно (слишком рано или слишком поздно: \
погрешность +-час). Так что в момент, когда бот считает, что остались лишь сутки, он отправляет сообщение с просьбой \
кому-либо сверить время по боту и время по игре. Для отображения списка текущих npc можно воспользоваться командой \
<code>/get_npc_list</code>. Предпологается, что под списком будут кнопки, позволяющие отредактировать время того или \
иного строения, но я всё никак не успеваю это реализовать, а уже существующая функциональность нужна как никогда. Так \
что, пока что я буду удалять неправильных npc вручную через админку.
- <i>(рег)</i> <i>(гр)</i> /new_npc - сообщить о новом временном строении
- <i>(рег)</i> <i>(гр|лс)</i> /get_npc_list - получить список существующих временных строений"""
            ]

            mes = ''
            mes_len = 0
            for block in blocks:
                block_len = len(block)
                if mes_len + block_len + 2 > constants.MAX_MESSAGE_LENGTH:
                    reply(mes, parse_mode=ParseMode.HTML)
                    mes = block
                    mes_len = block_len
                else:
                    mes = f'{mes}\n\n{block}'
                    mes_len += block_len + 2
            if mes:
                reply(mes, parse_mode=ParseMode.HTML)

        @groups_only('Разрешается устанавливать уведомления только из группы гильдии')
        @group_registered
        def set_additional_notifications(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            string = ''.join(context.args)
            regexp = re.compile(r'^(?:\+\d+[mh](?:\[(\d+|\*)\])?)*$')
            if regexp.match(string) is None:
                reply('Неверный формат. Пример правильного формата: +5m +10m +15m[3] +1h[*]')
                return
            guild.additional_notifications = string
            guild.save()
            reply('Сохранён новый график дополнительных уведомлений при несборе ресурсов.')

        @group_registered
        @private_guild_choice
        def get_additional_notifications(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            if guild.additional_notifications:
                n = f'<pre>{guild.additional_notifications}</pre>'
            else:
                n = "<i>не задан</i>"
            reply(f'Текущий график дополнительных уведомлений: {n}', parse_mode=ParseMode.HTML)

        @privates_only(f'Устанавливать имя можно только в [личной переписке]({updater.bot.link}) с ботом',
                       parse_mode=ParseMode.MARKDOWN_V2)
        @private_guild_choice
        def set_display_name(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            name = " ".join(context.args)[:16]
            if name == "":
                reply('Отредактируйте сообщение с командой, дописав свой ник (разделите команду и ник пробелом).')
                return
            tuser.set_display_name_for_guild(name, guild)

            def e(s):
                return escape_markdown(s, version=2)
            feedback = f'Установлено имя *{e(name)}* для гильдии _{e(guild.name)}_\\.\n'

            bot = context.bot
            bot_member = bot.get_chat_member(guild.chat_id, bot.id)  # check if bot is a member(maybe someone kicked us)
            if bot_member.can_promote_members and bot_member.can_change_info:
                user_member = bot.get_chat_member(guild.chat_id, tuser.chat_id)
                if user_member.status == ChatMember.CREATOR:
                    feedback += f'Обнаружено, что Вы \\- создатель чата гильдии\\. Я не могу установить Вам титул, ' \
                                f'т\\.к\\. это разрешено только Вам\\.'
                else:
                    if user_member.status != ChatMember.ADMINISTRATOR:
                        bot.promote_chat_member(guild.chat_id, tuser.chat_id, can_change_info=True)
                    result = bot.set_chat_administrator_custom_title(guild.chat_id, tuser.chat_id, name)
                    if result:
                        feedback += f'Так же оно было успешно установлено титулом в чат гильдии\\.'
                    else:
                        feedback += f'По некоторой причине не получилось установить его титулом в чат гильдии\\.'
            else:
                feedback += f'Если в чате гильдии боту выдадут права __Change group info__ и __Add new admins__, то ' \
                            f'он сможет автоматически ставить отображаемое имя в титул участника группы\\.'
            reply(feedback, parse_mode=ParseMode.MARKDOWN_V2)

        @groups_only('Сообщать о новом временном строении можно только в группе гильдии')
        @group_registered
        def new_npc(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            """/new_npc Вербовщик 6d12h"""
            usage = 'Первым аргументом необходимо задать оставшееся время, а именно: сплошную строку, состоящую из ' \
                    'пар (число, буква), где буква - одна из "m", "h", "d". Оставшееся будет использоваться как ' \
                    'название. Можете просто отредактировать неверную команду. Пример использования: ' \
                    '<pre>/new_npc 6d12h Вербовщик</pre>'
            if len(context.args) < 2:
                reply(usage, parse_mode=ParseMode.HTML)
                return
            time = context.args[0]
            regexp = re.compile(r'(\d+)([mhd])')
            if re.match(f'^(?:{regexp.pattern})+$', time) is None:
                reply(usage, parse_mode=ParseMode.HTML)
                return
            minutes = 0
            for n, s in regexp.findall(time):
                n = int(n)
                if s == 'h':
                    n *= 60
                elif s == 'd':
                    n *= 60 * 24
                minutes += n
            caption = " ".join(context.args[1:])
            ended = update.effective_message.date + timezone.timedelta(minutes=minutes)
            npc = TemporaryNPC.create(caption, by=tuser, in_guild=guild, ended_at=ended)
            logger.info(f'create TemporaryNPC pk {npc.pk}.')
            reply('Успешно зафиксировано новое временное строение! Можете следить за оставшимся временем с помощью '
                  'команды /get_npc_list')

        @private_guild_choice
        @group_registered
        def get_npc_list(update: Update, context: CallbackContext, reply=None, guild=None, tuser=None):
            # text = "\n".join(
            #     [f'({i+1}) {str(npc.at - timezone.now()).split(".")[0]} {npc.caption}'
            #      for i, npc in enumerate(TemporaryNPC.objects.filter(in_guild=guild, expired=False).order_by('-at'))]
            # )
            text = ''
            now = timezone.now()
            zero = timezone.timedelta()
            for i, npc in enumerate(TemporaryNPC.objects.filter(in_guild=guild, expired=False).order_by('at')):
                delta = npc.at - now
                delta_text = ''
                if delta < zero:
                    delta = zero
                if delta.days > 0:
                    delta_text += f'{delta.days}d'
                hours = delta.seconds // 3600
                if hours > 0:
                    delta_text += f' {hours}h'
                minutes = delta.seconds % 3600 // 60
                if minutes > 0:
                    delta_text += f' {minutes}m'
                # seconds = delta.seconds % 60
                # if seconds > 0:
                #     delta_text += f' {seconds}s'
                if delta_text == '':
                    delta_text = "<i>ожидание сообщения об окончании</i>"

                text += f'<code>{i+1}</code> <u>{delta_text}</u> <i>{npc.caption}</i>\n'
            if text == '':
                text += 'пока что пуст. Добавьте новых с помощью /new_npc'

            reply(f'Список NPC:\n{text}', parse_mode=ParseMode.HTML)
            # TODO: i need some method to change npc's remaining time

        @Log(at_start=True, at_finish=True)
        def chat_migration(update: Update, context: CallbackContext, reply=None):
            m = update.message
            old_id = m.migrate_from_chat_id or m.chat_id
            new_id = m.migrate_to_chat_id or m.chat_id

            try:
                guild = Guild.objects.get(chat_id=old_id)
                guild.chat_id = new_id
                guild.save()
                logger.info(f'guild pk {guild.pk} migrated from {old_id} to {new_id}')
            except Guild.DoesNotExist:
                pass

        @Log(at_start=True, level="ERROR")
        def error(update: Update, context: CallbackContext, reply=None):
            payload = ''
            if isinstance(context.error, ChatMigrated):
                payload += f" MIGRATE. update: {update}"
            devs = [163127202]
            trace = "".join(traceback.format_tb(sys.exc_info()[2]))
            if update.effective_user:
                payload += f' with the user {mention_html(int(update.effective_user.id), update.effective_user.name)}'
            if update.effective_chat:
                payload += f' within the chat <i>{update.effective_chat.title}</i>'
                if update.effective_chat.username:
                    payload += f' (@{update.effective_chat.username})'
            trace = trace.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
            text = f'The error <code>{context.error}</code> happened{payload}. The full traceback:\n\n<pre>' \
                   f'<code class="language-python">{trace}</code></pre>'
            try:
                for dev in devs:
                    context.bot.send_message(dev, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f'can\'t send dev message due to "{e}":\n{text}')
            # logger.error(f"error in update {update} and context {context}")
            print('Error. look in the log')
            raise

        def failed(update: Update, context: CallbackContext):
            raise

        dispatcher.add_handler(CommandHandler('start', start))

        dispatcher.add_handler(CommandHandler('collect', collect))
        dispatcher.add_handler(CallbackQueryHandler(guild_callback))

        dispatcher.add_handler(CommandHandler('register', register, pass_args=True))
        dispatcher.add_handler(CommandHandler('help', help_command))
        dispatcher.add_handler(CommandHandler('set_additional_notifications',
                                              set_additional_notifications,
                                              pass_args=True))
        dispatcher.add_handler(CommandHandler('get_additional_notifications', get_additional_notifications))
        dispatcher.add_handler(CommandHandler('set_display_name', set_display_name, pass_args=True))
        dispatcher.add_handler(CommandHandler('new_npc', new_npc, pass_args=True))
        dispatcher.add_handler(CommandHandler('get_npc_list', get_npc_list))

        # TODO: /schedule command - all the future notifications
        # TODO: /stats command
        # TODO: bot set own command list
        # TODO: webhook sending to discord
        dispatcher.add_handler(MessageHandler(Filters.status_update.migrate, chat_migration))
        dispatcher.add_handler(CommandHandler('failed', failed))
        dispatcher.add_error_handler(error)

        print('starting the bot... Ctrl-C to exit')
        logger.info("start bot polling")
        updater.start_polling()
        updater.idle()
        logger.info("stop bot polling")


if __name__ == '__main__':
    command = Command()
