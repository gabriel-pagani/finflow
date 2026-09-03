# Escrita à mão, no mesmo formato do que o makemigrations geraria.

import django.db.models.deletion
from django.db import migrations, models

import app.models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0016_message_visible'),
    ]

    operations = [
        migrations.CreateModel(
            name='Attachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('image', 'Imagem'), ('audio', 'Áudio')], max_length=10, verbose_name='Tipo')),
                ('file', models.FileField(max_length=200, upload_to=app.models.attachment_path, verbose_name='Arquivo')),
                ('mime', models.CharField(max_length=60, verbose_name='Tipo de Conteúdo')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='Criado em')),
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='attachment', to='app.message', verbose_name='Mensagem')),
            ],
            options={
                'verbose_name': 'Anexo',
                'verbose_name_plural': 'Anexos',
            },
        ),
    ]
