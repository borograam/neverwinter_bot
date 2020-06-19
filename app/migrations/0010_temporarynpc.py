# Generated by Django 3.0.6 on 2020-06-15 01:01

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0009_guild_members'),
    ]

    operations = [
        migrations.CreateModel(
            name='TemporaryNPC',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at', models.DateTimeField()),
                ('caption', models.CharField(max_length=50)),
                ('by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='app.TelegramUser')),
                ('in_guild', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='app.Guild')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]