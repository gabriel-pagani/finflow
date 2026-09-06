from django.contrib.auth.hashers import Argon2PasswordHasher


class CustomArgon2PasswordHasher(Argon2PasswordHasher):
    memory_cost = 262144
    time_cost = 10
    parallelism = 16
