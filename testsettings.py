DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

MIDDLEWARE_CLASSES = ()

INSTALLED_APPS = (
    'django.contrib.contenttypes', # for django.contrib.auth
    'django.contrib.auth', # for RequestFactoryTestCase
    'django_goodies',
    'django_goodies.tests',
)

SECRET_KEY = 'abcde12345'
