# -*- coding: utf-8 -*-
"""
Fabfile template for deploying django apps on webfaction using gunicorn,
and supervisor.
"""


from fabric.api import *
from fabric.contrib.files import upload_template, exists, append
import xmlrpclib
import sys

import string, random

try:
    from fabsettings import (WF_HOST,
                             PROJECT_NAME,
                             PROJECT_DIR_NAME,
                             PROJECT_PARENT_DIR,
                             PROJECT_DIR,
                             PROJECT_DJANGO_DIR,
                             PROJECT_SETTINGS_MODULE,
                             REPOSITORY,
                             USER,
                             PASSWORD,
                             VIRTUALENVS,
                             SETTINGS_SUBDIR,
                             )
except ImportError:
    print "ImportError: Couldn't find fabsettings.py, it either does not exist or giving import problems (missing settings)"
    sys.exit(1)

env.hosts                       = [WF_HOST]
env.user                        = USER
env.password                    = PASSWORD
env.home                        = "/home/%s" % USER
env.project_name                = PROJECT_NAME
env.project_dir_name            = PROJECT_DIR_NAME
env.project_parent_dir          = PROJECT_PARENT_DIR
env.project_dir                 = PROJECT_DIR
env.project_django_dir          = PROJECT_DJANGO_DIR
env.project_settings_module     = PROJECT_SETTINGS_MODULE
env.repo                        = REPOSITORY
env.webfaction_app_dir          = env.home + '/webapps/' + env.project_name
env.settings_dir                = env.webfaction_app_dir + '/' + SETTINGS_SUBDIR
env.supervisor_dir              = env.home + '/webapps/supervisor'
env.virtualenv_dir              = VIRTUALENVS
env.supervisor_ve_dir           = env.virtualenv_dir + '/supervisor'


def deploy():
    bootstrap()
    
    if not exists(env.supervisor_dir):
        install_supervisor()
    
    install_app()


def bootstrap():
    run('mkdir -p %s/lib/python2.7' % env.home)
    run('easy_install-2.7 pip')
    run('pip-2.7 install virtualenv virtualenvwrapper')


def install_app():
    """Installs the django project in its own wf app and virtualenv
    """
    response = _webfaction_create_app(env.project_name)
    env.app_port = response['port']

    # upload template to supervisor conf
    upload_template('templates/gunicorn.conf',
                    '{0}/conf.d/{1}.conf'.format(env.supervisor_dir, env.project_name),
                    {
                        'project': env.project_name,
                        'project_django_dir': env.project_django_dir,
                        'webfaction_app_dir': env.project_dir,  # Todo: is this correct???
                        'virtualenv': '{0}/{1}'.format(env.virtualenv_dir, env.project_name),
                        'port': env.app_port,
                        'user': env.user,
                    }
                    )

    with cd(env.project_parent_dir):
        if not exists(env.project_dir):
            run('git clone {0} {1}'.format(env.repo, env.project_dir))

    #_create_ve(env.project_name)
    reload_app()
    restart_app()

def install_supervisor():
    """Installs supervisor in its wf app and own virtualenv
    """
    response = _webfaction_create_app("supervisor")
    env.supervisor_port = response['port']
    _create_ve('supervisor')
    if not exists(env.supervisor_ve_dir + 'bin/supervisord'):
        _ve_run('supervisor', 'pip install supervisor')
    # uplaod supervisor.conf template
    upload_template('templates/supervisord.conf',
                     '%s/supervisord.conf' % env.supervisor_dir,
                    {
                        'user':     env.user,
                        'password': env.password,
                        'port':     env.supervisor_port,
                        'dir':      env.supervisor_dir,
                    },
                    )

    # upload and install crontab
    upload_template('templates/start_supervisor.sh',
                    '%s/start_supervisor.sh' % env.supervisor_dir,
                    {
                        'user':         env.user,
                        'virtualenv':   env.supervisor_ve_dir,
                    },
                    mode=0750,
                    )



    # add to crontab

    filename = ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(7))
    run('crontab -l > /tmp/%s' % filename)
    append('/tmp/%s' % filename, '*/10 * * * * %s/start_supervisor.sh start' % env.supervisor_dir)
    run('crontab /tmp/%s' % filename)


    # create supervisor/conf.d
    with cd(env.supervisor_dir):
        run('mkdir conf.d')

    with cd(env.supervisor_dir):
        with settings(warn_only=True):
            run('./start_supervisor.sh stop && ./start_supervisor.sh start')


def reload_app(arg=None):
    """Pulls app and refreshes requirements"""

    with cd(env.project_dir):
        run('git pull')

    if arg != "quick":
        with cd(env.project_dir):
            # _ve_run(env.project_name, "pip install gunicorn")
            _ve_run(env.project_name, "pip install -r requirements/production.txt")
        with cd(env.project_django_dir):
            _ve_run(env.project_name, "python manage.py syncdb --settings={0}".format(env.project_settings_module))
            _ve_run(env.project_name, "python manage.py migrate --settings={0}".format(env.project_settings_module))
            _ve_run(env.project_name, "python manage.py collectstatic --settings={0}".format(env.project_settings_module))

    restart_app()


def restart_app():
    """Restarts the app using supervisorctl"""

    with cd(env.supervisor_dir):
        _ve_run('supervisor', 'supervisorctl reread && supervisorctl reload')
        _ve_run('supervisor', 'supervisorctl restart %s' % env.project_name)


### Helper functions

def _create_ve(name):
    """creates virtualenv using virtualenvwrapper
    """
    if not exists(env.virtualenv_dir + '/name'):
        with cd(env.virtualenv_dir):
            run('mkvirtualenv -p /usr/local/bin/python2.7 --no-site-packages {0}'.format(name))
    else:
        print "Virtualenv with name %s already exists. Skipping." % name


def _ve_run(ve, cmd):
    """virtualenv wrapper for fabric commands
    """
    run("""source %s/%s/bin/activate && %s""" % (env.virtualenv_dir, ve, cmd))


def _webfaction_create_app(app):
    """creates a "custom app with port" app on webfaction using the webfaction public API.
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    try:
        response = server.create_app(session_id, app, 'custom_app_with_port', False, '')
        print "App on webfaction created: %s" % response
        return response

    except xmlrpclib.Fault:
        print "Could not create app on webfaction %s, app name maybe already in use" % app
        sys.exit(1)


def print_working_dir():
    """
    REMOVE THIS. It just test the server connection.
    """
    with cd(env.project_dir):
        with prefix('workon {0}'.format(env.project_name)):
            run('pwd')

