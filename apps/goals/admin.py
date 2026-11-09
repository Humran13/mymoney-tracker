from django.contrib import admin

from .models import GoalAllocation, SavingsGoal

admin.site.register(SavingsGoal)
admin.site.register(GoalAllocation)
