from ethereum.config import default_config
from pyethapp.config import update_config_with_defaults, get_default_config
from ethereum.slogging import get_logger
from ethereum.tools import tester
import pytest
import shutil
import tempfile
from devp2p.app import BaseApp
from pyethapp.eth_service import ChainService
from pyethapp.db_service import DBService
from pyethapp.accounts import Account, AccountsService
from pyethapp.validator_service import ValidatorService
from ethereum.utils import encode_hex

log = get_logger('tests.validator_service')

@pytest.fixture()
def app(request):
    config = {
        'accounts': {
            'keystore_dir': tempfile.mkdtemp(),
        },
        'data_dir': str(tempfile.gettempdir()),
        'db': {'implementation': 'EphemDB'},
        'pow': {'activated': False},
        'p2p': {
            'min_peers': 0,
            'max_peers': 0,
            'listen_port': 29873
        },
        'discovery': {
            'boostrap_nodes': [],
            'listen_port': 29873
        },
        'eth': {
            'block': {  # reduced difficulty, increased gas limit, allocations to test accounts
                'GENESIS_DIFFICULTY': 1,
                'BLOCK_DIFF_FACTOR': 2,  # greater than difficulty, thus difficulty is constant
                'GENESIS_GAS_LIMIT': 3141592,
                'GENESIS_INITIAL_ALLOC': {
                    encode_hex(tester.accounts[0]): {'balance': 10**24},
                }
            }
        },
        'jsonrpc': {'listen_port': 29873},
        'validate': [encode_hex(tester.accounts[0])],
    }

    services = [
        DBService,
        # AccountsService,
        ChainService,
        ValidatorService,
        ]
    update_config_with_defaults(config, get_default_config([BaseApp] + services))
    update_config_with_defaults(config, {'eth': {'block': default_config}})
    app = BaseApp(config)

    # Add AccountsService first and initialize with coinbase account
    AccountsService.register_with_app(app)
    app.services.accounts.add_account(Account.new('', tester.keys[0]), store=False)

    for service in services:
        service.register_with_app(app)

    def fin():
        # cleanup temporary keystore directory
        assert app.config['accounts']['keystore_dir'].startswith(tempfile.gettempdir())
        shutil.rmtree(app.config['accounts']['keystore_dir'])
        log.debug('cleaned temporary keystore dir', dir=app.config['accounts']['keystore_dir'])
    request.addfinalizer(fin)

    return app

def test_foo(app):
    assert True
