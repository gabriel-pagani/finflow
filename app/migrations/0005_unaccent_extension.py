from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0004_remove_installment_method_remove_installment_type'),
    ]

    operations = [
        UnaccentExtension(),
    ]
