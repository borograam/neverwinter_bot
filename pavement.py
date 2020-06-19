from paver.easy import *


@task
def prepare_ignored_files():
    logs = path("logs/")
    if not logs.exists():
        logs.mkdir()
    local_settings = path("influence_bot/settings/local.py")
    if not path.exists(local_settings):
        local_settings.touch()


@task
@needs(['prepare_ignored_files'])
def runbot():
    """run telegram bot (polling)"""
    d = path("logs/")
    if not path.exists(d):
        path.mkdir(d)
    sh("./manage.py bot")


@task
@needs(['prepare_ignored_files'])
@cmdopts([
    ('development', 'd', 'is it a development running')
])
def runserver(options):
    """run django development server"""
    addr = "10.10.0.1:8000" if not options.development else ""
    sh("./manage.py runserver {}".format(addr))


@task
def runscheduler():
    """run rq-scheduler"""
    sh("./manage.py rqscheduler")


@task
def runworker():
    """run rq worker"""
    sh("./manage.py rqworker")


@task
def shell():
    """run django shell"""
    sh("./manage.py shell")
