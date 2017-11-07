from __future__ import print_function
from devp2p.service import BaseService
from ethereum.slogging import get_logger
from ethereum.messages import apply_transaction
from ethereum.tools import tester
from ethereum import transactions
from ethereum import abi, utils
from ethereum.hybrid_casper import casper_utils

log = get_logger('validator')

class ValidatorService(BaseService):

    name = 'validator'
    default_config = dict(validator=dict(
        validating=False,
        privkey='',
        deposit_size=0
    ))

    def __init__(self, app):
        super(ValidatorService, self).__init__(app)
        log.info('whats up whats up')

        self.config = app.config

        self.chainservice = app.services.chain
        self.chain = self.chainservice.chain
        self.valcode_tx = None
        self.deposit_tx = None
        self.valcode_addr = None
        self.has_broadcasted_deposit = False
        self.votes = dict()
        self.epoch_length = self.chain.env.config['EPOCH_LENGTH']
        # self.chain.time = lambda: int(time.time())

        if app.config['validate']:
            self.coinbase = app.services.accounts.find(app.config['validate'][0])
            self.validating = True
        else:
            self.validating = False

        app.services.chain.on_new_head_cbs.append(self.on_new_head)

    def on_new_head(self, block):
        if not self.validating:
            return
        if self.app.services.chain.is_syncing:
            return
        state = self.chain.mk_poststate_of_blockhash(block.hash)
        self.update(state)

    def broadcast_deposit(self):
        if not self.valcode_tx or not self.deposit_tx:
            nonce = self.chain.state.get_nonce(self.coinbase.address)
            # Generate transactions
            valcode_tx = self.mk_validation_code_tx(nonce)
            valcode_addr = utils.mk_contract_address(self.coinbase.address, nonce)
            deposit_tx = self.mk_deposit_tx(3 * 10**18, valcode_addr, nonce+1)
            # Verify the transactions pass
            temp_state = self.chain.state.ephemeral_clone()
            valcode_success, o1 = apply_transaction(temp_state, valcode_tx)
            deposit_success, o2 = apply_transaction(temp_state, deposit_tx)
            self.valcode_tx = valcode_tx
            log.info('Valcode Tx generated: {}'.format(str(valcode_tx)))
            self.valcode_addr = valcode_addr
            self.deposit_tx = deposit_tx
            log.info('Deposit Tx generated: {}'.format(str(deposit_tx)))
        self.chainservice.broadcast_transaction(valcode_tx)

    def update(self, state):
        if not self.valcode_tx or not self.deposit_tx:
            self.broadcast_deposit()
        if not self.has_broadcasted_deposit and state.get_code(self.valcode_addr):
            log.info('Found code!')
            self.chainservice.broadcast_transaction(self.deposit_tx)
            self.has_broadcasted_deposit = True
        log.info('Validator index: {}'.format(self.get_validator_index(state)))

        casper = tester.ABIContract(tester.State(state), casper_utils.casper_abi,
                                    self.chain.casper_address)
        try:
            log.info('&&& '.format())
            log.info('Vote percent: {} - Recommended Source: {} - Current Epoch: {}'
                     .format(casper.get_main_hash_voted_frac(),
                             casper.get_recommended_source_epoch(), casper.get_current_epoch()))
            is_justified = casper.get_votes__is_justified(casper.get_current_epoch())
            is_finalized = casper.get_votes__is_finalized(casper.get_current_epoch()-1)
            if is_justified:
                log.info('Justified epoch: {}'.format(casper.get_current_epoch()))
            if is_finalized:
                log.info('Finalized epoch: {}'.format(casper.get_current_epoch()-1))
        except:
            log.info('&&& Vote frac failed')

        # Vote
        # Generate vote messages and broadcast if possible
        vote_msg = self.generate_vote_message(state)
        if vote_msg:
            vote_tx = self.mk_vote_tx(vote_msg)
            self.chainservice.broadcast_transaction(vote_tx)
            log.info('Sent vote! Tx: {}'.format(str(vote_tx)))

    def get_recommended_casper_msg_contents(self, state, casper, validator_index):
        curepoch = casper.get_current_epoch()
        if curepoch == 0:
            return None, None, None
        tgthash = casper.get_recommended_target_hash()
        sourceepoch = casper.get_recommended_source_epoch()
        return tgthash, curepoch, sourceepoch
        # return \
        #     casper.get_recommended_target_hash(), casper.get_current_epoch(), \
        #     casper.get_recommended_source_epoch()

    def epoch_blockhash(self, state, epoch):
        if epoch == 0:
            return b'\x00' * 32
        return state.prev_headers[epoch*self.epoch_length * -1 - 1].hash

    def generate_vote_message(self, state):
        epoch = state.block_number // self.epoch_length
        # NO_DBL_VOTE: Don't vote if we have already
        if epoch in self.votes:
            return None
        # TODO: Check for NO_SURROUND_VOTE
        # Create a Casper contract which we can use to get related values
        casper = tester.ABIContract(tester.State(state), casper_utils.casper_abi, self.chain.casper_address)
        # Get the ancestry hash and source ancestry hash
        validator_index = self.get_validator_index(state)
        target_hash, epoch, source_epoch = self.get_recommended_casper_msg_contents(state, casper, validator_index)
        if target_hash is None:
            return None
        vote_msg = casper_utils.mk_vote(validator_index, target_hash, epoch, source_epoch, self.coinbase.privkey)
        try:  # Attempt to submit the vote, to make sure that it is justified
            casper.vote(vote_msg)
        except tester.TransactionFailed:
            log.info('Vote failed! Validator {} - validator start {} - valcode addr {}'
                     .format(self.get_validator_index(state),
                             casper.get_validators__start_dynasty(validator_index),
                             utils.encode_hex(self.valcode_addr)))
            return None
        # Save the vote message we generated
        self.votes[epoch] = vote_msg
        log.info('Vote submitted: validator %d - epoch %d - source_epoch %d - hash %s' %
                 (self.get_validator_index(state), epoch, source_epoch, utils.encode_hex(self.epoch_blockhash(state, epoch))))
        return vote_msg

    def get_validator_index(self, state):
        t = tester.State(state.ephemeral_clone())
        casper = tester.ABIContract(t, casper_utils.casper_abi, self.chain.casper_address)
        if self.valcode_addr is None:
            raise Exception('Valcode address not set')
        try:
            return casper.get_validator_indexes(self.coinbase.address)
        except tester.TransactionFailed:
            return None

    def mk_transaction(self, to=b'\x00' * 20, value=0, data=b'',
                       gasprice=tester.GASPRICE, startgas=tester.STARTGAS, nonce=None):
        if nonce is None:
            nonce = self.chain.state.get_nonce(self.coinbase.address)
        tx = transactions.Transaction(nonce, gasprice, startgas, to, value, data)
        self.coinbase.sign_tx(tx)
        return tx

    def mk_validation_code_tx(self, nonce):
        valcode_tx = self.mk_transaction('', 0, casper_utils.mk_validation_code(self.coinbase.address), nonce=nonce)
        return valcode_tx

    def mk_deposit_tx(self, value, valcode_addr, nonce):
        casper_ct = abi.ContractTranslator(casper_utils.casper_abi)
        deposit_func = casper_ct.encode('deposit', [valcode_addr, self.coinbase.address])
        deposit_tx = self.mk_transaction(self.chain.casper_address, value, deposit_func, nonce=nonce)
        return deposit_tx

    def mk_vote_tx(self, vote_msg):
        casper_ct = abi.ContractTranslator(casper_utils.casper_abi)
        vote_func = casper_ct.encode('vote', [vote_msg])
        vote_tx = self.mk_transaction(to=self.chain.casper_address, value=0, startgas=1000000, data=vote_func)
        return vote_tx
