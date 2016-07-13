########
# Copyright (c) 2016 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import tempfile
import time

from cloudify import ctx
from cloudify.decorators import operation
from cloudify.utils import LocalCommandRunner, CommandExecutionException
from cloudify.exceptions import NonRecoverableError

IMAGE = 'cloudify/centos:7'
PUBLIC_KEY_CONTAINER_PATH = '/etc/ssh/ssh_host_rsa_key.pub'
PRIVATE_KEY_CONTAINER_PATH = '/etc/ssh/ssh_host_rsa_key'


@operation
def start(image=IMAGE, **_):
    container_id = _start_container(image)
    _extract_container_ip(container_id)
    install_agent_script = ctx.agent.init_script({'user': 'root'})
    if install_agent_script:
        _init_script_agent_setup(container_id, install_agent_script)
    else:
        _remote_agent_setup(container_id)


@operation
def delete(**_):
    container_id = ctx.instance.runtime_properties.pop('container_id', None)
    ctx.instance.runtime_properties.pop('ip', None)
    if not container_id:
        return
    _delete_container(container_id)
    key_path = _key_path()
    if os.path.exists(key_path):
        os.remove(key_path)


def _start_container(image):
    container_id = _docker(
        'run',
        '--privileged --expose=1-65535 -d {0}'.format(image))
    ctx.instance.runtime_properties['container_id'] = container_id
    return container_id


def _delete_container(container_id):
    try:
        _docker('rm', '-f {0}'.format(container_id))
    except CommandExecutionException as e:
        ctx.logger.warn('Failed removing container {0}: '.format(e))


def _extract_container_ip(container_id):
    container_ip = _docker(
        'inspect',
        "-f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' " +
        container_id)
    ctx.instance.runtime_properties['ip'] = container_ip


def _init_script_agent_setup(container_id, install_agent_script):
    fd, host_install_agent_script_path = tempfile.mkstemp()
    os.close(fd)
    container_install_agent_script_path = '/root/agent_install_script.sh'
    with open(host_install_agent_script_path, 'w') as f:
        f.write(install_agent_script)
    try:
        _docker('cp', '{0} {1}:{2}'.format(
            host_install_agent_script_path,
            container_id,
            container_install_agent_script_path))
        _docker_exec(container_id, 'chmod +x {0}'.format(
            container_install_agent_script_path))
        _docker_exec(container_id, container_install_agent_script_path)
    except BaseException as e:
        tpe, value, tb = sys.exc_info()
        raise NonRecoverableError, NonRecoverableError(str(e)), tb
    finally:
        os.remove(host_install_agent_script_path)


def _remote_agent_setup(container_id):
    _wait_for_ssh_setup(container_id)
    _docker_exec(container_id, 'cp {0} /root/.ssh/authorized_keys'.format(
        PUBLIC_KEY_CONTAINER_PATH))
    private_key = _docker_exec(container_id, 'cat {0}'.format(
        PRIVATE_KEY_CONTAINER_PATH))
    key_path = _key_path()
    with open(key_path, 'w') as f:
        f.write(private_key)
    agent_config = ctx.instance.runtime_properties.setdefault('cloudify_agent',
                                                              {})
    agent_config.update({
        'key': key_path,
        'user': 'root',
    })


def _wait_for_ssh_setup(container_id):
    attempt = 0
    while attempt < 10:
        try:
            return _docker_exec(container_id,
                                'cat {0}'.format(PUBLIC_KEY_CONTAINER_PATH))
        except CommandExecutionException:
            attempt += 1
            time.sleep(1)
    raise


def _docker_exec(container_id, args):
    return _docker('exec', '{0} {1}'.format(container_id, args))


def _docker(subcommand, args):
    return _run('docker {0} {1}'.format(subcommand, args))


def _run(command):
    return LocalCommandRunner(logger=ctx.logger).run(command).std_out.strip()


def _key_path():
    return os.path.join(ctx.plugin.workdir, '{0}.key'.format(ctx.instance.id))