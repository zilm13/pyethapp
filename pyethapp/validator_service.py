from __future__ import print_function
from enum import Enum
from devp2p.service import BaseService
from ethereum.slogging import get_logger
from ethereum.tools import tester
from ethereum import transactions, abi, utils
from ethereum.hybrid_casper import casper_utils

log = get_logger('eth.validator')


class ValidatorState(Enum):
    uninitiated = 1             # Check if logged in, and if not deploy a valcode contract
    waiting_for_valcode = 2     # Wait for valcode ct to be included, then submit deposit
    waiting_for_login = 3       # Wait for validator to login, then change state to `voting`
    voting = 4                  # Vote on each new epoch
    waiting_for_log_out = 5
    logged_out = 6


# TODO: Check if user is logged in & reinitialize based on contract

class ValidatorService(BaseService):

    name = 'validator'
    default_config = dict(validator=dict(
        deposit_size=0
    ))

    def __init__(self, app):
        super(ValidatorService, self).__init__(app)
        # Check if we should actually validate, if not return
        if not app.config['validate']:
            return
        log.info('Validator enabled!')

        self.config = app.config
        self.chainservice = app.services.chain
        self.chain = self.chainservice.chain
        self.deposit_size = self.config['deposit_size']
        self.valcode_addr = None
        self.epoch_length = self.chain.env.config['EPOCH_LENGTH']
        self.votes = dict()
        self.latest_target_epoch = -1
        self.latest_source_epoch = -1
        self.coinbase = app.services.accounts.find(app.config['validate'][0])
        # Set new block callback. This will trigger validation logic
        app.services.chain.on_new_head_cbs.append(self.on_new_head)
        # Set up the validator's state & handlers
        self.current_state = ValidatorState.uninitiated

        self.handlers = {
            ValidatorState.uninitiated: self.check_logged_in,
            ValidatorState.waiting_for_valcode: self.check_valcode,
            ValidatorState.waiting_for_login: self.check_logged_in,
            ValidatorState.voting: self.vote
        }

    def on_new_head(self, block):
        if self.app.services.chain.is_syncing:
            return
        casper = tester.ABIContract(tester.State(self.chain.state.ephemeral_clone()),
                                    casper_utils.casper_abi, self.chain.casper_address)
        self.log_casper_info(casper)
        self.handlers[self.current_state](casper)

    def check_logged_in(self, casper):
        validator_index = self.get_validator_index(casper)
        # (1) Check if the validator has ever deposited funds
        if not validator_index and self.deposit_size:
            # The validator hasn't deposited funds but deposit flag is set, so deposit!
            self.broadcast_valcode_tx()
            self.current_state = ValidatorState.waiting_for_valcode
        elif not validator_index:
            # The validator hasn't deposited funds and we have no intention to, so return!
            return
        # (2) Check if the validator is logged in
        if not self.is_logged_in(casper, casper.get_current_epoch(), validator_index):
            # The validator isn't logged in, so return!
            return
        # The validator is logged in, so set the state to voting!
        log.info('Changing validator state to voting')
        self.current_state = ValidatorState.voting

    def check_valcode(self, casper):
        if not self.chain.state.get_code(self.valcode_addr):
            # Valcode still not deployed!
            return
        # Valcode deployed! Let's deposit
        self.broadcast_deposit_tx()
        self.current_state = ValidatorState.waiting_for_login

    def vote(self, casper):
        log.info('Attempting to vote')
        epoch = self.chain.state.block_number // self.epoch_length
        if self.chain.state.block_number % self.epoch_length <= self.epoch_length / 4:
            return None
        # NO_DBL_VOTE: Don't vote if we have already
        if epoch in self.votes:
            return None
        # Get the ancestry hash and source ancestry hash
        validator_index = self.get_validator_index(casper)
        target_hash, epoch, source_epoch = self.recommended_vote_contents(casper, validator_index)
        if target_hash is None:
            return None
        # Prevent NO_SURROUND slash
        if epoch < self.latest_target_epoch or source_epoch < self.latest_source_epoch:
            return None
        # Verify that we are either in the current dynasty or prev dynasty
        if not self.is_logged_in(casper, epoch, validator_index):
            log.info('Validator not logged in yet!')
            return None
        vote_msg = casper_utils.mk_vote(validator_index, target_hash, epoch,
                                        source_epoch, self.coinbase.privkey)
        # Save the vote message we generated
        self.votes[epoch] = vote_msg
        self.latest_target_epoch = epoch
        self.latest_source_epoch = source_epoch
        # Send the vote!
        vote_tx = self.mk_vote_tx(vote_msg)
        self.chainservice.broadcast_transaction(vote_tx)
        log.info('Sent vote! Tx: {}'.format(str(vote_tx)))
        log.info('Vote submitted: validator %d - epoch %d - source_epoch %d - hash %s' %
                 (self.get_validator_index(casper),
                  epoch, source_epoch, utils.encode_hex(target_hash)))

    def log_casper_info(self, casper):
        ce = casper.get_current_epoch()
        total_curdyn_deposits = casper.get_total_curdyn_deposits()
        total_prevdyn_deposits = casper.get_total_prevdyn_deposits()
        voted_curdyn_deposits = casper.get_votes__cur_dyn_votes(ce, casper.get_expected_source_epoch()) * casper.get_deposit_scale_factor(ce)
        voted_prevdyn_deposits = casper.get_votes__prev_dyn_votes(ce, casper.get_expected_source_epoch()) * casper.get_deposit_scale_factor(ce)
        last_finalized_epoch, last_justified_epoch = casper.get_last_finalized_epoch(), casper.get_last_justified_epoch()
        try:
            mhvf = casper.get_main_hash_voted_frac()
        except:
            mhvf = -1
        log.info('CASPER STATUS: epoch %d, %r / %.3f ETH voted from current dynasty, '
                 '%r / %.3f ETH voted from previous dynasty, last finalized epoch %d justified %d,'
                 'expected source epoch %d moose %.3f' %
                 (ce, voted_curdyn_deposits / 10**18, total_curdyn_deposits / 10**18,
                     voted_prevdyn_deposits / 10**18, total_prevdyn_deposits / 10**18,
                  last_finalized_epoch, last_justified_epoch, casper.get_expected_source_epoch(), mhvf
                  ))

    def broadcast_valcode_tx(self):
        valcode_tx = self.mk_transaction('', 0,
                                         casper_utils.mk_validation_code(self.coinbase.address))
        nonce = self.chain.state.get_nonce(self.coinbase.address)
        self.valcode_addr = utils.mk_contract_address(self.coinbase.address, nonce)
        log.info('Broadcasting valcode tx with nonce: {}'.format(valcode_tx.nonce))
        self.chainservice.broadcast_transaction(valcode_tx)

    def broadcast_deposit_tx(self):
        # Create deposit transaction
        casper_ct = abi.ContractTranslator(casper_utils.casper_abi)
        deposit_func = casper_ct.encode('deposit', [self.valcode_addr, self.coinbase.address])
        deposit_tx = self.mk_transaction(self.chain.casper_address,
                                         self.deposit_size, deposit_func)
        # Broadcast it!
        log.info('Broadcasting deposit tx with nonce: {}'.format(deposit_tx.nonce))
        self.deposit_size = None
        self.chainservice.broadcast_transaction(deposit_tx)

    def mk_transaction(self, to=b'\x00' * 20, value=0, data=b'',
                       gasprice=tester.GASPRICE, startgas=tester.STARTGAS, nonce=None):
        if nonce is None:
            nonce = self.chain.state.get_nonce(self.coinbase.address)
        tx = transactions.Transaction(nonce, gasprice, startgas, to, value, data)
        self.coinbase.sign_tx(tx)
        return tx

    def is_logged_in(self, casper, target_epoch, validator_index):
        start_dynasty = casper.get_validators__start_dynasty(validator_index)
        end_dynasty = casper.get_validators__end_dynasty(validator_index)
        current_dynasty = casper.get_dynasty_in_epoch(target_epoch)
        past_dynasty = current_dynasty - 1
        in_current_dynasty = ((start_dynasty <= current_dynasty) and
                              (current_dynasty < end_dynasty))
        in_prev_dynasty = ((start_dynasty <= past_dynasty) and (past_dynasty < end_dynasty))
        if not (in_current_dynasty or in_prev_dynasty):
            return False
        return True

    def get_validator_index(self, casper):
        try:
            return casper.get_validator_indexes(self.coinbase.address)
        except tester.TransactionFailed:
            return None

    def recommended_vote_contents(self, casper, validator_index):
        current_epoch = casper.get_current_epoch()
        if current_epoch == 0:
            return None, None, None
        # NOTE: Using `epoch_blockhash` because currently calls to `blockhash` within contracts
        # in the ephemeral state are off by one, so we can't use `get_recommended_target_hash()` :(
        target_hash = self.epoch_blockhash(current_epoch)
        source_epoch = casper.get_recommended_source_epoch()
        return target_hash, current_epoch, source_epoch

    def epoch_blockhash(self, epoch):
        if epoch == 0:
            return b'\x00' * 32
        return self.chain.get_block_by_number(epoch*self.epoch_length-1).hash

    def mk_vote_tx(self, vote_msg):
        casper_ct = abi.ContractTranslator(casper_utils.casper_abi)
        vote_func = casper_ct.encode('vote', [vote_msg])
        vote_tx = self.mk_transaction(to=self.chain.casper_address,
                                      value=0, startgas=1000000, data=vote_func)
        return vote_tx
