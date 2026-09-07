from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0006_remove_applicationskillrequirement_application_skill_requirement_label_not_blank_and_more'),
        ('skills', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='applicationskillrequirement',
            name='application_skill_requirement_unique_concept',
        ),
        migrations.RemoveConstraint(
            model_name='applicationskillrequirement',
            name='application_skill_requirement_label_not_blank',
        ),
        migrations.RemoveField(
            model_name='applicationskillrequirement',
            name='label',
        ),
        migrations.RemoveField(
            model_name='applicationskillrequirement',
            name='concept',
        ),
        migrations.AddField(
            model_name='applicationskillrequirement',
            name='alias',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='application_requirements', to='skills.skillalias'),
            preserve_default=False,
        ),
    ]
