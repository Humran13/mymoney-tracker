from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User, UserProfile, UserSettings


@receiver(post_save, sender=User)
def create_profile_and_settings(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
        UserSettings.objects.create(user=instance)
