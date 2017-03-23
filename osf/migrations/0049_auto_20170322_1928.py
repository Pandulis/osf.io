# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2017-03-23 00:28
from __future__ import unicode_literals

from django.db import migrations
import osf.utils.datetime_aware_jsonfield


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0048_add_partial_index_to_files'),
    ]

    operations = [
        migrations.AlterField(
            model_name='basefilenode',
            name='_history',
            field=osf.utils.datetime_aware_jsonfield.DateTimeAwareJSONField(blank=True, default=list),
        ),
        migrations.AlterUniqueTogether(
            name='basefilenode',
            unique_together=set([]),
        ),
    ]
