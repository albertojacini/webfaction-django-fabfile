# -*- coding: utf-8 -*-

"""
Fabfile template for deploying django apps on Webfaction using gunicorn and supervisor.
"""


from fabric.api import *
from fabric.contrib.files import upload_template, exists, append
from fabric.colors import red, green, blue, cyan, magenta, white, yellow
from fabric.contrib.project import rsync_project
import xmlrpclib
import sys

import string, random

try:
    from fabsettings import (WF_HOST,
                             IP_HOST,
                             PROJECT_NAME,
                             PROJECT_DIR_NAME,
                             PROJECT_PARENT_DIR,
                             PROJECT_DIR,
                             PROJECT_DJANGO_DIR,
                             PROJECT_SETTINGS_MODULE,
                             PROJECT_MEDIA,
                             REPOSITORY,
                             USER,
                             PASSWORD,
                             VIRTUALENVS,
                             SETTINGS_SUBDIR,
                             LOCAL_PROJECT_DIR,
                             PG_DATABASE_NAME,
                             PG_DATABASE_USER,
                             HOST,
                             APACHE_DIR,
                             )
except ImportError:
    print "ImportError: Couldn't find fabsettings.py, it either does not exist or giving import problems (missing settings)"
    sys.exit(1)

env.hosts                       = [WF_HOST]
env.ip_host                     = IP_HOST
env.user                        = USER
env.password                    = PASSWORD
env.home                        = "/home/%s" % USER
env.project_name                = PROJECT_NAME
env.project_dir_name            = PROJECT_DIR_NAME
env.project_parent_dir          = PROJECT_PARENT_DIR
env.project_dir                 = PROJECT_DIR
env.project_django_dir          = PROJECT_DJANGO_DIR
env.project_settings_module     = PROJECT_SETTINGS_MODULE
env.project_media               = PROJECT_MEDIA
env.apache_dir                  = APACHE_DIR
env.repo                        = REPOSITORY
env.pg_database_name            = PG_DATABASE_NAME
env.pg_database_user            = PG_DATABASE_USER
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
    run('pip install virtualenv virtualenvwrapper')


def install_app():
    """Installs the django project in its own wf app and virtualenv
    """
    run('mkdir -p %s/media' % env.project_parent_dir)
    upload_secrets()
    response = webfaction_create_app(env.project_name)
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
                        'password': env.password,
                        'user': env.user,
                    }
                    )

    with cd(env.project_parent_dir):
        if not exists(env.project_dir):
            run('git clone {0} {1}'.format(env.repo, env.project_dir))

    _create_ve(env.project_name)
    webfaction_configuration(env.project_name)
    reload_app()
    restart_app()

def upload_secrets():
    """upload secrets.json from local directory
    """
    upload_template(LOCAL_PROJECT_DIR + '/secrets.json', env.project_parent_dir)


def install_supervisor():
    """Installs supervisor in its wf app and own virtualenv
    """
    response = webfaction_create_app("supervisor")
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
            _ve_run(env.project_name, "python manage.py collectstatic --noinput --settings={0}".format(env.project_settings_module))

    restart_app()


def restart_app():
    """Restarts the app using supervisorctl"""

    with cd(env.supervisor_dir):
        _ve_run('supervisor', 'supervisorctl reread && supervisorctl reload')
        _ve_run('supervisor', 'supervisorctl restart %s' % env.project_name)

## restart Apache

def restart_apache():
    """
    Restart Apache.
    """
    print "Restarting Apache ..."
    with cd(env.apache_dir):
        run('./restart')


### Helper functions

def _create_ve(name):
    """Creates virtualenv using virtualenvwrapper
    """
    if not exists(env.virtualenv_dir + '/name'):
        with cd(env.virtualenv_dir):
            run('mkvirtualenv -p /usr/local/bin/python2.7 --no-site-packages {0}'.format(name))
    else:
        print "Virtualenv with name %s already exists. Skipping." % name


def _ve_run(ve, cmd):
    """Virtualenv wrapper for fabric commands
    """
    run("""source %s/%s/bin/activate && %s""" % (env.virtualenv_dir, ve, cmd))


def webfaction_configuration(app):
    webfaction_create_app_media(app)
    webfaction_create_app_static(app)
    webfaction_create_domain(app)
    webfaction_create_website(app)
    webfaction_create_postgres_db(app)

# -----------------------------------------------------------------------------
# CREATE APP
#  -----------------------------------------------------------------------------

def webfaction_create_app(app):
    """Creates a "custom app with port" app on webfaction using the webfaction public API.
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    try:
        response = server.create_app(
            session_id,
            app,
            'custom_app_with_port',
            False,
            ''
        )
        print "App on webfaction created: %s" % response
        return response

    except xmlrpclib.Fault:
        print "Could not create app on webfaction %s, app name maybe already in use" % app
        print red("If the app already exists, you must remove it and recreate it manually (otherwise ports can be \
                   automathically detected.")
        sys.exit(1)

# -----------------------------------------------------------------------------
# CREATE DOMAIN
# -----------------------------------------------------------------------------

def webfaction_create_domain(app):
    """Creates default domain on webfaction using the webfaction public API.
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    domain = '%s.webfactional.com' % env.user

    try:
        response = server.create_domain(session_id, domain, app)
        print green("Default domain on webfaction created: %s" % response)
        return response

    except xmlrpclib.Fault:
        print red("Could not create domain on webfaction %s" % domain)
        print red("The domain might already exists.")
        # sys.exit(1)


# -----------------------------------------------------------------------------
# CREATE MEDIA APP
# -----------------------------------------------------------------------------

def webfaction_create_app_media(app):
    """Creates a simlynk static only app on webfaction using the webfaction public API.
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    app_name = '%s_media' % app
    try:
        response = server.create_app(
            session_id,
            app_name,
            'symlink_static_only',
            False,
            env.project_parent_dir + '/media/'
        )
        print green("App media on webfaction created: %s" % response)
        return response

    except xmlrpclib.Fault:
        print red("Could not create app media on webfaction %s, app name maybe already in use" % app_name)
        print red("An app with this name might already exists.")
        # sys.exit(1)

# -----------------------------------------------------------------------------
# CREATE STATIC APP
# -----------------------------------------------------------------------------

def webfaction_create_app_static(app):
    """Creates a simlynk static only app on webfaction using the webfaction public API.
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    app_name = '%s_static' % app
    try:
        response = server.create_app(
            session_id,
            app_name,
            'symlink_static_only',
            False,
            env.project_django_dir + '/static_root/'
        )
        print green("App static on webfaction created: %s" % response)
        return response

    except xmlrpclib.Fault:
        print red("Could not create app media on webfaction %s, app name maybe already in use" % app_name)
        print red("An app with this name might already exists.")
        # sys.exit(1)

# -----------------------------------------------------------------------------
# CREATE WEBSITE
# -----------------------------------------------------------------------------

def webfaction_create_website(website):
    """Creates website on webfaction and refers apps
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)

    try:
        response = server.create_website(
            session_id,
            website,
            env.ip_host,
            False,
            ['%s.%s.webfactional.com' % (website, env.user)],
            [env.project_name, '/'],
            [env.project_name + '_static', '/static'],
            [env.project_name + '_media', '/media'])
        print(green("Website created: %s" % response))
        return response

    except xmlrpclib.Fault:
        print red("Could not create %s website on webfaction " % website)
        print red("A website with this name might already exists.")
        # sys.exit(1)


# -----------------------------------------------------------------------------
# CREATE POSTGRES DB
# -----------------------------------------------------------------------------

def webfaction_create_postgres_db(db):
    """Creates postgres db
    """
    server = xmlrpclib.ServerProxy('https://api.webfaction.com/')
    session_id, account = server.login(USER, PASSWORD)
    try:
        response = server.create_db(
            session_id,
            db,
            'postgresql',
            PASSWORD
        )
        print(green("Postgres database %s created" % response['name']))
        return response

    except xmlrpclib.Fault:
        print red("Could not create postgres database on webfaction ")
        print red("A database with this name might already exists.")
        # sys.exit(1)


def print_working_dir():
    """
    TODO: REMOVE THIS. It just tests the server connection.
    """
    with cd(env.project_dir):
        with prefix('workon {0}'.format(env.project_name)):
            run('pwd')

# -----------------------------------------------------------------------------
# Backup media and postgres db
# -----------------------------------------------------------------------------

def backup():
    rsync_from_remote()
    dump()


def dump():
    pg_dump()
    copy_pg_dump_to_local()


def rsync_from_remote():
    """rsync media from remote
    """
    try:
        local('rsync -avz {0}@{1}:{2} {3}' .format(env.user, env.hosts[0], env.project_parent_dir + '/media', LOCAL_PROJECT_DIR))
        print green("Synchronized {0} media from {1} " .format(env.project_name, env.hosts[0]))
    except:
        print red("Could not syncronize {0} media from {1} " .format(env.project_name, env.hosts[0]))


def pg_dump():
    """dump remote postgres db
    """
    try:
        run('pg_dump -U {0} -W {1} > {1}.sql' .format(env.pg_database_user, env.pg_database_name,))
        print green('{0} database dumped' .format(env.pg_database_name))
    except:
        print red('could not dump the {0} database' .format(env.pg_database_name))
        sys.exit(1)


def copy_pg_dump_to_local():
    """copy dumped posgres db, copy on local machine and remove the remote one
    """
    try:
        local('scp {0}:{1}.sql {2}' .format(HOST, env.pg_database_user, LOCAL_PROJECT_DIR,))
        run('rm {0}.sql' .format(env.pg_database_name))
    except:
        print red('could not copy on local machine and remove remotly {0} database' .format(env.pg_database_name))
        sys.exit(1)




# -----------------------------------------------------------------------------
# Load database and media on remote
# -----------------------------------------------------------------------------


def load_to_remote():
    copy_database_to_remote()
    load_remote_database()
    rsync_to_remote()


def copy_database_to_remote():
    """copy db on remote machine
    """
    try:
        local('scp {0}/{1}.psql {2}:' .format(LOCAL_PROJECT_DIR, env.pg_database_name, HOST))
    except:
        print red('could not copy on remote machine local {0} database' .format(env.pg_database_name))
        sys.exit(1)


def load_remote_database():
    """Load db on remote machine
    """
    try:
        run('psql -f {0}.psql -U {1} -W {0}' .format(env.pg_database_name, env.pg_database_user))
    except:
        print red('could not load on remote machine {0} database' .format(env.pg_database_name))
        sys.exit(1)


def rsync_to_remote():
    """rsync media to remote machine
    """
    try:
        local('rsync -avz {0}/media {1}:{2}' .format(LOCAL_PROJECT_DIR, HOST, env.project_parent_dir))
        print green("Synchronized {0} media from local to {1} " .format(env.project_name, HOST))
    except:
        print red("Could not syncronize {0} media from {1} " .format(env.project_name, HOST))


