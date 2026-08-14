import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0006_projectskill"),
        ("skills", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Skill",
            new_name="ProfileSkill",
        ),
        migrations.AlterModelTable(
            name="profileskill",
            table="profiles_skill",
        ),
        migrations.AddField(
            model_name="profileskill",
            name="concept",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="profile_skills",
                to="skills.skillconcept",
            ),
        ),
        migrations.AddField(
            model_name="profileskill",
            name="label",
            field=models.CharField(max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="profileskill",
            name="normalized_label",
            field=models.CharField(editable=False, max_length=200, null=True),
        ),
    ]
