from django.contrib import admin
from .models import TelegramUser, InfluenceCollection, Guild
# Register your models here.


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('name', 'first_name', 'last_name')
    search_fields = ['name', 'first_name']


@admin.register(Guild)
class GuildAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(InfluenceCollection)
class InfluenceCollectionAdmin(admin.ModelAdmin):
    list_display = ('in_guild', 'at', 'waiting_to_notify', 'notified')
    list_filter = ('waiting_to_notify', 'notified')
    search_fields = ['in_guild__name']

