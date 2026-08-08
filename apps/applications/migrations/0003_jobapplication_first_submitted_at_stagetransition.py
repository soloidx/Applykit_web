import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0002_jobapplication'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobapplication',
            name='first_submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='StageTransition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_stage', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('interviewing', 'Interviewing'), ('offer', 'Offer'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')], max_length=16)),
                ('to_stage', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('interviewing', 'Interviewing'), ('offer', 'Offer'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')], max_length=16)),
                ('occurred_at', models.DateTimeField(auto_now_add=True)),
                ('application', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stage_transitions', to='applications.jobapplication')),
            ],
            options={
                'ordering': ['occurred_at', 'pk'],
            },
        ),
    ]
