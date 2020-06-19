from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from .models import TelegramUser, ResourceCollection, Guild, Notification, TemporaryNPC, GuildMembership


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('name', 'first_name', 'last_name')
    search_fields = ['name', 'first_name']
    # fields = ('django', 'first_name', 'last_name', 'link', 'name', 'chat_id', 'influence_collection_set')


class GuildMembershipInline(admin.TabularInline):
    model = GuildMembership
    can_delete = False

    def has_add_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Guild)
class GuildAdmin(admin.ModelAdmin):
    search_fields = ['name']
    list_display = ('name', 'member_number')
    inlines = [GuildMembershipInline]

    def member_number(self, obj):
        return obj.members.count()


class NotificationInline(GenericTabularInline):
    model = Notification
    readonly_fields = ('time', 'job_id', 'canceled', 'notified')
    exclude = ('number',)
    can_delete = False

    def has_add_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ResourceCollection)
class ResourceCollectionAdmin(admin.ModelAdmin):
    list_display = ('pk', 'in_guild', 'by', 'at', 'will_notify', 'notified')
    list_filter = ('in_guild',)
    inlines = [NotificationInline]

    def will_notify(self, obj):
        return "+" if obj.notifications.filter(notified=False, canceled=False).exists() else "-"

    def notified(self, obj):
        return "+" if obj.notifications.filter(notified=True).exists() else "-"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('pk', 'content_type', 'caused_by', 'time', 'canceled', 'notified')


@admin.register(TemporaryNPC)
class TemporaryNPCAdmin(admin.ModelAdmin):
    list_display = ('pk', 'caption', 'in_guild', 'at', 'by')
    list_filter = ('in_guild',)
    inlines = [NotificationInline]
