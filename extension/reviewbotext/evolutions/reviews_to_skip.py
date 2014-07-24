from django_evolution.mutations import AddField
from django.db import models

MUTATIONS = [
    AddField('ReviewBotTool', 'reviews_to_skip', models.CharField, initial="",
             max_length=512),
]
