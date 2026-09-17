from django.contrib.auth.hashers import Argon2PasswordHasher


class CustomArgon2PasswordHasher(Argon2PasswordHasher):
    memory_cost = 65536
    time_cost = 3
    parallelism = 4
