# Generated by Django 3.0.6 on 2020-05-31 21:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0004_auto_20200531_1829'),
    ]

    operations = [
        migrations.AddField(
            model_name='guild',
            name='additional_notifications',
            field=models.CharField(default='', max_length=100),
        ),
    ]
