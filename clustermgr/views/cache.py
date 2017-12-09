"""A Flask blueprint with the views and logic dealing with the Cache Management
of Gluu Servers"""
from flask import Blueprint, render_template, url_for, flash, redirect, \
    request, session, jsonify

from clustermgr.models import Server, AppConfiguration
from clustermgr.tasks.cache import get_cache_methods, install_cache_components, \
    configure_cache_cluster, restart_services, install_redis_stunnel, \
    install_twemproxy_stunnel
from clustermgr.extensions import wlogger
from ..core.license import license_reminder
from ..core.license import license_manager


cache_mgr = Blueprint('cache_mgr', __name__, template_folder='templates')
cache_mgr.before_request(license_reminder)


@cache_mgr.route('/')
@license_manager.license_required
def index():
    servers = Server.query.all()
    appconf = AppConfiguration.query.first()
    if not appconf:
        flash("The application needs to be configured first. Kindly set the "
              "values before attempting clustering.", "warning")
        return redirect(url_for("index.app_configuration"))

    if not servers:
        flash("Add servers to the cluster before attempting to manage cache",
              "warning")
        return redirect(url_for('index.home'))

    version = int(appconf.gluu_version.replace(".", ""))
    if version < 311:
        flash("Cache Management is available only for clusters configured with"
              " Gluu Server version 3.1.1 and above", "danger")
        return redirect(url_for('index.home'))

    return render_template('cache_index.html', servers=servers)


@cache_mgr.route('/refresh_methods')
@license_manager.license_required
def refresh_methods():
    task = get_cache_methods.delay()
    return jsonify({'task_id': task.id})


@cache_mgr.route('/change/', methods=['GET', 'POST'])
@license_manager.license_required
def change():
    servers = Server.query.all()
    method = 'STANDALONE'
    tasks = list()
    for server in servers:
        tasks.append(dict(task=install_redis_stunnel.delay(server.id),
                          server=server))

    if method == 'STANDALONE':
        appconf = AppConfiguration.query.first()
        mock_server = Server()
        mock_server.hostname = appconf.nginx_host
        mock_server.id = 0
        tasks.append(dict(task=install_twemproxy_stunnel.delay(),
                          server=mock_server))

    return render_template('cache_installs.html', tasks=tasks)


@cache_mgr.route('/configure/<method>/')
@license_manager.license_required
def configure(method):
    task = configure_cache_cluster.delay(method)
    servers = Server.query.filter(Server.redis.is_(True)).filter(
        Server.stunnel.is_(True)).all()
    return render_template('cache_logger.html', method=method, servers=servers,
                           step=2, task_id=task.id)


@cache_mgr.route('/finish_clustering/<method>/')
@license_manager.license_required
def finish_clustering(method):
    servers = Server.query.filter(Server.redis.is_(True)).filter(
        Server.stunnel.is_(True)).all()
    task = restart_services.delay(method)
    return render_template('cache_logger.html', servers=servers, step=3,
                           task_id=task.id)


@cache_mgr.route('/task_status/<task_id>/')
def task_status(task_id):
    meta = wlogger.get_all_meta(task_id)
    status = dict()
    status['progress'] = int(meta['complete']) / int(meta['todo']) * 100
    status['errors'] = 0
    status['warnings'] = 0
    status['latest'] = 'Connecting to server ...'

    msgs = wlogger.get_messages(task_id)
    for msg in msgs:
        if msg['level'] == 'info':
            status['latest'] = msg['msg']
        if msg['level'] == 'error' or msg['level'] == 'fail':
            status['errors'] += 1
        if msg['level'] == 'warning':
            status['warnings'] += 1

    return jsonify(status)
