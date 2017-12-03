from itertools import count
import pytest
import shutil
import tempfile
from devp2p.service import BaseService
from ethereum.config import default_config
from pyethapp.config import update_config_with_defaults, get_default_config
from ethereum.slogging import get_logger, configure_logging
from ethereum.tools import tester
from ethereum.pow.ethpow import mine
from ethereum.tests.hybrid_casper.testing_lang import TestLangHybrid
from ethereum.utils import encode_hex
# from devp2p.app import BaseApp
from pyethapp.app import EthApp
from pyethapp.eth_service import ChainService
from pyethapp.db_service import DBService
from pyethapp.accounts import Account, AccountsService
from pyethapp.validator_service import ValidatorService
from pyethapp.pow_service import PoWService

log = get_logger('tests.validator_service')
configure_logging('validator:debug')

class PeerManagerMock(BaseService):
    name = 'peermanager'

    def broadcast(*args, **kwargs):
        pass

@pytest.fixture()
def test_app(request, tmpdir):
    class TestApp(EthApp):

        # def start(self):
        #     super(TestApp, self).start()
        #     log.debug('adding test accounts')
        #     # high balance account
        #     # self.services.accounts.add_account(Account.new('', tester.keys[0]), store=False)
        #     # # low balance account
        #     # self.services.accounts.add_account(Account.new('', tester.keys[1]), store=False)
        #     # # locked account
        #     # locked_account = Account.new('', tester.keys[2])
        #     # locked_account.lock()
        #     # self.services.accounts.add_account(locked_account, store=False)
        #     assert set(acct.address for acct in self.services.accounts) == set(tester.accounts[:1])

        def mine_next_block(self):
            """Mine until a valid nonce is found.

            :returns: the new head
            """
            log.debug('mining next block')
            block = self.services.chain.head_candidate
            chain = self.services.chain.chain
            head_number = chain.head.number
            delta_nonce = 10**6
            for start_nonce in count(0, delta_nonce):
                bin_nonce, mixhash = mine(block.number, block.difficulty, block.mining_hash,
                                          start_nonce=start_nonce, rounds=delta_nonce)
                if bin_nonce:
                    break
            self.services.chain.add_mined_block(block)
            self.services.pow.recv_found_nonce(bin_nonce, mixhash, block.mining_hash)
            if len(chain.time_queue) > 0:
                # If we mine two blocks within one second, pyethereum will
                # force the new block's timestamp to be in the future (see
                # ethereum1_setup_block()), and when we try to add that block
                # to the chain (via Chain.add_block()), it will be put in a
                # queue for later processing. Since we need to ensure the
                # block has been added before we continue the test, we
                # have to manually process the time queue.
                log.debug('block mined too fast, processing time queue')
                chain.process_time_queue(new_time=block.timestamp)
            log.debug('block mined')
            assert chain.head.difficulty == 1
            assert chain.head.number == head_number + 1
            return chain.head

    config = {
        # 'accounts': {
        #     'keystore_dir': tempfile.mkdtemp(),
        # },
        # 'data_dir': str(tempfile.gettempdir()),
        'data_dir': str(tmpdir),
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
        # 'validate': [tester.accounts[0]],
        # 'validate': [],
    }

    services = [
        DBService,
        # AccountsService,
        ChainService,
        PoWService,
        PeerManagerMock,
        ValidatorService,
        ]
    update_config_with_defaults(config, get_default_config([TestApp] + services))
    update_config_with_defaults(config, {'eth': {'block': default_config}})
    app = TestApp(config)

    # Add AccountsService first and initialize with coinbase account
    AccountsService.register_with_app(app)
    app.services.accounts.add_account(Account.new('', tester.keys[0]), store=False)

    for service in services:
        service.register_with_app(app)

    def fin():
        log.debug('stopping test app')
        app.stop()
        # # cleanup temporary keystore directory
        # assert app.config['accounts']['keystore_dir'].startswith(tempfile.gettempdir())
        # shutil.rmtree(app.config['accounts']['keystore_dir'])
        # log.debug('cleaned temporary keystore dir', dir=app.config['accounts']['keystore_dir'])
    request.addfinalizer(fin)

    return app

def test_generate_valcode(test_app):
    # test = TestLangHybrid(5, 25, 0.02, 0.002)
    # test.parse('B B')
    # app.services.validator.chain = test.t.chain
    # app.services.validator.chain.on_new_head_cbs.append(app.services.validator.on_new_head)
    # test.parse('B1')
    # test_chain = tester.Chain()
    # test_chain.mine(30)
    test_app.mine_next_block()

    assert True
