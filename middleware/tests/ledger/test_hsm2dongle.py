# The MIT License (MIT)
#
# Copyright (c) 2021 RSK Labs Ltd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from unittest import TestCase
from unittest.mock import Mock, patch, call
from parameterized import parameterized
from ledger.hsm2dongle import (
    HSM2Dongle,
    HSM2DongleError,
    HSM2DongleCommError,
    HSM2DongleTimeoutError,
    HSM2DongleErrorResult,
)
from sgx.hsm2dongle import HSM2DongleSGX
from ledger.version import HSM2FirmwareVersion
from ledgerblue.commException import CommException
from enum import Enum

import logging

logging.disable(logging.CRITICAL)


class HSM2DongleTestMode(Enum):
    Ledger = "ledger"
    SGX = "sgx"


class TestHSM2DongleBase(TestCase):
    DONGLE_EXCHANGE_TIMEOUT = 10

    CHUNK_ERROR_MAPPINGS = [
        ("prot_invalid", 0x6B87, -4),
        ("rlp_invalid", 0x6B88, -5),
        ("block_too_old", 0x6B89, -5),
        ("block_too_short", 0x6B8A, -5),
        ("parent_hash_invalid", 0x6B8B, -5),
        ("block_num_invalid", 0x6B8D, -5),
        ("block_diff_invalid", 0x6B8E, -5),
        ("umm_root_invalid", 0x6B8F, -5),
        ("btc_header_invalid", 0x6B90, -5),
        ("merkle_proof_invalid", 0x6B91, -5),
        ("btc_cb_txn_invalid", 0x6B92, -6),
        ("mm_rlp_len_mismatch", 0x6B93, -5),
        ("btc_diff_mismatch", 0x6B94, -6),
        ("merkle_proof_mismatch", 0x6B95, -6),
        ("mm_hash_mismatch", 0x6B96, -6),
        ("merkle_proof_overflow", 0x6B97, -5),
        ("cb_txn_overflow", 0x6B98, -5),
        ("buffer_overflow", 0x6B99, -5),
        ("chain_mismatch", 0x6B9A, -7),
        ("total_diff_overflow", 0x6B9B, -8),
        ("cb_txn_hash_mismatch", 0x6B9D, -6),
        ("brothers_too_many", 0x6B9E, -9),
        ("brother_parent_mismatch", 0x6B9F, -9),
        ("brother_same_as_block", 0x6BA0, -9),
        ("brother_order_invalid", 0x6BA1, -9),
        ("unexpected", 0x6BFF, -10),
        ("error_response", bytes([0, 0, 0xFF]), -10),
    ]

    def get_test_mode(self):
        return HSM2DongleTestMode.Ledger

    @patch("ledger.hsm2dongle_tcp.getDongle")
    @patch("ledger.hsm2dongle.getDongle")
    def setUp(self, getDongleMock, getDongleTCPMock):
        if self.get_test_mode() == HSM2DongleTestMode.Ledger:
            self.dongle = Mock()
            self.getDongleMock = getDongleMock
            self.getDongleMock.return_value = self.dongle
            self.hsm2dongle = HSM2Dongle("a-debug-value")
            self.getDongleMock.assert_not_called()
            self.hsm2dongle.connect()
            self.getDongleMock.assert_called_with("a-debug-value")
            getDongleTCPMock.assert_not_called()
        elif self.get_test_mode() == HSM2DongleTestMode.SGX:
            self.dongle = Mock()
            self.getDongleMock = getDongleTCPMock
            self.getDongleMock.return_value = self.dongle
            self.hsm2dongle = HSM2DongleSGX("a-host", 1234, "a-debug-value")

            self.getDongleMock.assert_not_called()
            self.hsm2dongle.connect()
            self.getDongleMock.assert_called_with("a-host", 1234, "a-debug-value")
            self.assertEqual(self.hsm2dongle.dongle, self.dongle)
            getDongleMock.assert_not_called()
        else:
            raise RuntimeError(f"Unknown test mode: {self.get_test_mode()}")

    def buf(self, size):
        return bytes(map(lambda b: b % 256, range(size)))

    def parse_exchange_spec(self, spec, stop=None, replace=None):
        rqs = []
        rps = []
        rq = True
        stopped = False
        for line in spec:
            delim = ">" if rq else "<"
            delim_pos = line.find(delim)
            if delim_pos == -1:
                raise RuntimeError("Invalid spec prefix")
            name = line[:delim_pos].strip()
            if name == stop:
                if replace is not None:
                    (rqs if rq else rps).append(replace)
                stopped = True
                break
            (rqs if rq else rps).append(
                bytes.fromhex("80" + line[delim_pos+1:].replace(" ", ""))
            )
            rq = not rq

        if stop is not None and not stopped:
            raise RuntimeError(f"Invalid spec parsing: specified stop at '{stop}' "
                               "but exchange not found")
        return {"requests": rqs, "responses": rps}

    def spec_to_exchange(self, spec, trim=False):
        trim_length = spec[0][-1] if trim else 0
        block_size = len(spec[0]) - trim_length
        chunk_size = spec[1]
        exchanges = [bytes([0, 0, 0x04, chunk_size])]*(block_size//chunk_size)
        remaining = block_size - len(exchanges)*chunk_size
        exchanges = [bytes([0, 0, 0x03])] + exchanges + \
                    [bytes([0, 0, 0x04, remaining])]

        # Spec has brothers?
        if len(spec) == 3:
            exchanges += [bytes([0, 0, 0x07])]  # Request brother list metadata
        if len(spec) == 3 and spec[2] is not None:
            brother_count = len(spec[2][0])
            chunk_size = spec[2][1]
            for i in range(brother_count):
                brother_size = len(spec[2][0][i])
                bro_exchanges = [bytes([0, 0, 0x09, chunk_size])] * \
                    (brother_size//chunk_size)
                remaining = brother_size - len(bro_exchanges)*chunk_size
                exchanges += [bytes([0, 0, 0x08])] + \
                    bro_exchanges + \
                    [bytes([0, 0, 0x09, remaining])]

        return exchanges

    def assert_exchange(self, payloads, timeouts=None):
        def ensure_cla(bs):
            if bs[0] != 0x80:
                return bytes([0x80]) + bs
            return bs

        if timeouts is None:
            timeouts = [None]*len(payloads)
        calls = list(
            map(
                lambda z: call(
                    ensure_cla(bytes(z[0])),
                    timeout=(z[1] if z[1] is not None else self.DONGLE_EXCHANGE_TIMEOUT),
                ),
                zip(payloads, timeouts),
            ))

        self.assertEqual(
            len(payloads),
            len(self.dongle.exchange.call_args_list),
            msg="# of exchanges mismatch",
        )

        for i, c in enumerate(calls):
            if c != self.dongle.exchange.call_args_list[i]:
                print("E:", c)
                print("A:", self.dongle.exchange.call_args_list[i])
            self.assertEqual(
                c,
                self.dongle.exchange.call_args_list[i],
                msg="%dth exchange failed" % (i + 1),
            )

    def do_sign_auth(self, spec):
        return self.hsm2dongle.sign_authorized(
            key_id=spec["keyid"],
            rsk_tx_receipt=spec["receipt"],
            receipt_merkle_proof=spec["mp"],
            btc_tx=spec["tx"],
            input_index=spec["input"],
            sighash_computation_mode=spec["mode"],
            witness_script=spec["ws"],
            outpoint_value=spec["ov"],
        )

    def process_sign_auth_spec(self, spec, stop=None, replace=None):
        pex = self.parse_exchange_spec(spec["exchanges"], stop=stop, replace=replace)
        spec["requests"] = pex["requests"]
        spec["responses"] = pex["responses"]
        self.dongle.exchange.side_effect = spec["responses"]
        return spec


class TestHSM2Dongle(TestHSM2DongleBase):
    def test_dongle_error_codes(self):
        # Make sure enums are ok wrt signer definitions by testing a couple
        # of arbitrary values
        self.assertEqual(0x6B8C, self.hsm2dongle.ERR.ADVANCE.RECEIPT_ROOT_INVALID.value)
        self.assertEqual(0x6B93, self.hsm2dongle.ERR.ADVANCE.MM_RLP_LEN_MISMATCH.value)
        self.assertEqual(0x6BA1, self.hsm2dongle.ERR.ADVANCE.BROTHER_ORDER_INVALID.value)
        self.assertEqual(0x6A8F, self.hsm2dongle.ERR.SIGN.INVALID_PATH)
        self.assertEqual(
            0x6A97,
            self.hsm2dongle.ERR.SIGN.INVALID_SIGHASH_COMPUTATION_MODE.value
        )

    def test_connects_ok(self):
        self.assertEqual([call("a-debug-value")], self.getDongleMock.call_args_list)

    @patch("ledger.hsm2dongle.getDongle")
    def test_connects_error_comm(self, getDongleMock):
        getDongleMock.side_effect = CommException("a-message")
        with self.assertRaises(HSM2DongleCommError):
            self.hsm2dongle.connect()

    @patch("ledger.hsm2dongle.getDongle")
    def test_connects_error_other(self, getDongleMock):
        getDongleMock.side_effect = ValueError()
        with self.assertRaises(ValueError):
            self.hsm2dongle.connect()

    def test_get_current_mode(self):
        self.dongle.exchange.return_value = bytes([10, 2, 30])
        mode = self.hsm2dongle.get_current_mode()
        self.assertEqual(2, mode)
        self.assertEqual(self.hsm2dongle.MODE, type(mode))
        self.assert_exchange([[0x43]])

    def test_echo(self):
        self.dongle.exchange.return_value = bytes([0x80, 0x02, 0x41, 0x42, 0x43])
        self.assertTrue(self.hsm2dongle.echo())
        self.assert_exchange([[0x02, 0x41, 0x42, 0x43]])

    def test_echo_error(self):
        self.dongle.exchange.return_value = bytes([1, 2, 3])
        self.assertFalse(self.hsm2dongle.echo())
        self.assert_exchange([[0x02, 0x41, 0x42, 0x43]])

    def test_is_onboarded_yes(self):
        self.dongle.exchange.return_value = bytes([0, 1, 0])
        self.assertTrue(self.hsm2dongle.is_onboarded())
        self.assert_exchange([[0x06]])

    def test_is_onboarded_no(self):
        self.dongle.exchange.return_value = bytes([0, 0, 0])
        self.assertFalse(self.hsm2dongle.is_onboarded())
        self.assert_exchange([[0x06]])

    def test_onboard_ok(self):
        self.dongle.exchange.side_effect = [bytes([0])]*(32 + 5) + [bytes([0, 2, 0])]

        self.assertTrue(
            self.hsm2dongle.onboard(bytes(map(lambda i: i*2, range(32))), b"1234"))

        seed_exchanges = list(map(lambda i: [0x44, i, i*2], range(32)))
        pin_exchanges = [[0x41, 0, 4]] + list(
            map(lambda i: [0x41, i + 1, ord(str(i + 1))], range(4)))
        exchanges = seed_exchanges + pin_exchanges + [[0x07]]
        timeouts = [None]*len(exchanges)
        timeouts[-1] = HSM2Dongle.ONBOARDING.TIMEOUT
        self.assert_exchange(exchanges, timeouts)

    def test_onboard_wipe_error(self):
        self.dongle.exchange.side_effect = [bytes([0])]*(32 + 5) + [bytes([0, 1, 0])]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.onboard(bytes(map(lambda i: i*2, range(32))), b"1234")

        seed_exchanges = list(map(lambda i: [0x44, i, i*2], range(32)))
        pin_exchanges = [[0x41, 0, 4]] + list(
            map(lambda i: [0x41, i + 1, ord(str(i + 1))], range(4)))
        exchanges = seed_exchanges + pin_exchanges + [[0x07]]
        timeouts = [None]*len(exchanges)
        timeouts[-1] = HSM2Dongle.ONBOARDING.TIMEOUT
        self.assert_exchange(exchanges, timeouts)

    def test_onboard_pin_error(self):
        self.dongle.exchange.side_effect = [bytes([0])]*(32 + 3) + [
            CommException("an-error")
        ]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.onboard(bytes(map(lambda i: i*2, range(32))), b"1234")

        seed_exchanges = list(map(lambda i: [0x44, i, i*2], range(32)))
        pin_exchanges = [[0x41, 0, 4]] + list(
            map(lambda i: [0x41, i + 1, ord(str(i + 1))], range(3)))
        exchanges = seed_exchanges + pin_exchanges
        self.assert_exchange(exchanges)

    def test_onboard_seed_error(self):
        self.dongle.exchange.side_effect = [bytes([0])]*30 + [CommException("an-error")]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.onboard(bytes(map(lambda i: i*2, range(32))), b"1234")

        seed_exchanges = list(map(lambda i: [0x44, i, i*2], range(31)))
        self.assert_exchange(seed_exchanges)

    def test_unlock_ok(self):
        self.dongle.exchange.side_effect = [
            bytes([0]),
            bytes([1]),
            bytes([2]),
            bytes([0, 0, 1]),
        ]
        self.assertTrue(self.hsm2dongle.unlock(bytes([1, 2, 3])))
        self.assert_exchange([[0x41, 0, 1], [0x41, 1, 2], [0x41, 2, 3],
                              [0xFE, 0x00, 0x00]])

    def test_unlock_pinerror(self):
        self.dongle.exchange.side_effect = [
            bytes([0]),
            bytes([1]),
            bytes([2]),
            bytes([0, 0, 0]),
        ]
        self.assertFalse(self.hsm2dongle.unlock(bytes([1, 2, 3])))
        self.assert_exchange([[0x41, 0, 1], [0x41, 1, 2], [0x41, 2, 3],
                              [0xFE, 0x00, 0x00]])

    def test_new_pin(self):
        self.dongle.exchange.side_effect = [
            bytes([0]),
            bytes([1]),
            bytes([2]),
            bytes([3]),
            bytes([4]),
        ]
        self.hsm2dongle.new_pin(bytes([4, 5, 6]))
        self.assert_exchange([[0x41, 0, 3], [0x41, 1, 4], [0x41, 2, 5], [0x41, 3, 6],
                              [0x08]])

    def test_version(self):
        self.dongle.exchange.return_value = bytes([0, 0, 6, 7, 8])
        version = self.hsm2dongle.get_version()
        self.assertEqual(HSM2FirmwareVersion, type(version))
        self.assertEqual(6, version.major)
        self.assertEqual(7, version.minor)
        self.assertEqual(8, version.patch)
        self.assert_exchange([[0x06]])

    def test_retries(self):
        self.dongle.exchange.return_value = bytes([0, 0, 57])
        retries = self.hsm2dongle.get_retries()
        self.assertEqual(57, retries)
        self.assert_exchange([[0x45]])

    def test_exit_menu(self):
        self.dongle.exchange.return_value = bytes([0])
        self.hsm2dongle.exit_menu()
        self.assert_exchange([[0xFF, 0x00, 0x00]])

    def test_exit_menu_explicit_autoexec(self):
        self.dongle.exchange.return_value = bytes([0])
        self.hsm2dongle.exit_menu(autoexec=True)
        self.assert_exchange([[0xFF, 0x00, 0x00]])

    def test_exit_menu_no_autoexec(self):
        self.dongle.exchange.return_value = bytes([0])
        self.hsm2dongle.exit_menu(autoexec=False)
        self.assert_exchange([[0xFA, 0x00, 0x00]])

    def test_exit_app(self):
        self.dongle.exchange.side_effect = OSError("read error")
        with self.assertRaises(HSM2DongleCommError):
            self.hsm2dongle.exit_app()
        self.assert_exchange([[0xFF]])

    def test_get_public_key_ok(self):
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.dongle.exchange.return_value = bytes.fromhex("aabbccddee")
        self.assertEqual("aabbccddee", self.hsm2dongle.get_public_key(key_id))
        self.assert_exchange([[0x04, 0x11, 0x22, 0x33, 0x44]])

    def test_get_public_key_invalid_keyid(self):
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.dongle.exchange.side_effect = CommException("some message", 0x6A87)
        with self.assertRaises(HSM2DongleErrorResult):
            self.hsm2dongle.get_public_key(key_id)
        self.assert_exchange([[0x04, 0x11, 0x22, 0x33, 0x44]])

    def test_get_public_key_timeout(self):
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.dongle.exchange.side_effect = CommException("Timeout")
        with self.assertRaises(HSM2DongleTimeoutError):
            self.hsm2dongle.get_public_key(key_id)
        self.assert_exchange([[0x04, 0x11, 0x22, 0x33, 0x44]])

    def test_get_public_key_other_error(self):
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.dongle.exchange.side_effect = CommException("some other message", 0xFFFF)
        with self.assertRaises(HSM2DongleError):
            self.assertEqual("aabbccddee", self.hsm2dongle.get_public_key(key_id))
        self.assert_exchange([[0x04, 0x11, 0x22, 0x33, 0x44]])


class TestHSM2DongleSignUnauthorized(TestHSM2DongleBase):
    @patch("ledger.hsm2dongle.HSM2DongleSignature")
    def test_sign_unauthorized_ok(self, HSM2DongleSignatureMock):
        HSM2DongleSignatureMock.return_value = "the-signature"
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x81, 0x55, 0x66, 0x77, 0x88]),  # Response to path and hash
        ]
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.assertEqual(
            (True, "the-signature"),
            self.hsm2dongle.sign_unauthorized(key_id=key_id, hash="aabbccddeeff"),
        )

        self.assert_exchange([
            [
                0x02,
                0x01,
                0x11,
                0x22,
                0x33,
                0x44,
                0xAA,
                0xBB,
                0xCC,
                0xDD,
                0xEE,
                0xFF,
            ],  # Path and hash
        ])
        self.assertEqual(
            [call(bytes([0x55, 0x66, 0x77, 0x88]))],
            HSM2DongleSignatureMock.call_args_list,
        )

    @patch("ledger.hsm2dongle.HSM2DongleSignature")
    def test_sign_unauthorized_invalid_signature(self, HSM2DongleSignatureMock):
        HSM2DongleSignatureMock.side_effect = ValueError()
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x81, 0x55, 0x66, 0x77, 0x88]),  # Response to path and hash
        ]
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.assertEqual(
            (False, -10),
            self.hsm2dongle.sign_unauthorized(key_id=key_id, hash="aabbccddeeff"),
        )

        self.assert_exchange([
            [
                0x02,
                0x01,
                0x11,
                0x22,
                0x33,
                0x44,
                0xAA,
                0xBB,
                0xCC,
                0xDD,
                0xEE,
                0xFF,
            ],  # Path and hash
        ])
        self.assertEqual(
            [call(bytes([0x55, 0x66, 0x77, 0x88]))],
            HSM2DongleSignatureMock.call_args_list,
        )

    @parameterized.expand([
        ("data_size", 0x6A87, -5),
        ("data_size_noauth", 0x6A91, -5),
        ("invalid_path", 0x6A8F, -1),
        ("data_size_auth", 0x6A90, -1),
        ("unknown", 0x6AFF, -10),
        ("btc_tx", [0, 0, 0x02], -5),
        ("unexpected", [0, 0, 0xAA], -10),
    ])
    def test_sign_unauthorized_dongle_error_result(self, _, device_error,
                                                   expected_response):
        if type(device_error) == int:
            last_exchange = CommException("msg", device_error)
        else:
            last_exchange = bytes(device_error)
        self.dongle.exchange.side_effect = [last_exchange]  # Response to path and hash
        key_id = Mock(**{"to_binary.return_value": bytes.fromhex("11223344")})
        self.assertEqual(
            (False, expected_response),
            self.hsm2dongle.sign_unauthorized(key_id=key_id, hash="aabbccddeeff"),
        )

        self.assert_exchange([
            [
                0x02,
                0x01,
                0x11,
                0x22,
                0x33,
                0x44,
                0xAA,
                0xBB,
                0xCC,
                0xDD,
                0xEE,
                0xFF,
            ],  # Path and hash
        ])

    def test_sign_unauthorized_invalid_hash(self):
        self.assertEqual(
            (False, -5),
            self.hsm2dongle.sign_unauthorized(key_id="doesn't matter", hash="not-a-hex"),
        )

        self.assertFalse(self.dongle.exchange.called)


class TestHSM2DongleBlockchainState(TestHSM2DongleBase):
    def test_get_blockchain_state_ok(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x01, 0x01]) +
            bytes.fromhex("11"*32),  # Response to get best_block
            bytes([0, 0, 0x01, 0x02]) +
            bytes.fromhex("22"*32),  # Response to get newest_valid_block
            bytes([0, 0, 0x01, 0x03]) +
            bytes.fromhex("33"*32),  # Response to get ancestor_block
            bytes([0, 0, 0x01, 0x05]) +
            bytes.fromhex("44"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x81]) +
            bytes.fromhex("55"*32),  # Response to get updating.best_block
            bytes([0, 0, 0x01, 0x82]) +
            bytes.fromhex("66"*32),  # Response to get updating.newest_valid_block
            bytes([0, 0, 0x01, 0x84]) +
            bytes.fromhex("77"*32),  # Response to get updating.next_expected_block
            bytes([0, 0, 0x02]) +
            bytes.fromhex("112233445566"),  # Response to get difficulty
            bytes([0, 0, 0x03, 0x00, 0xFF, 0xFF]),  # Response to get flags
        ]
        self.assertEqual(
            {
                "best_block":
                "11"*32,
                "newest_valid_block":
                "22"*32,
                "ancestor_block":
                "33"*32,
                "ancestor_receipts_root":
                "44"*32,
                "updating.best_block":
                "55"*32,
                "updating.newest_valid_block":
                "66"*32,
                "updating.next_expected_block":
                "77"*32,
                "updating.total_difficulty":
                int.from_bytes(
                    bytes.fromhex("112233445566"), byteorder="big", signed=False),
                "updating.in_progress":
                False,
                "updating.already_validated":
                True,
                "updating.found_best_block":
                True,
            },
            self.hsm2dongle.get_blockchain_state(),
        )

        self.assert_exchange([
            [0x20, 0x01, 0x01],
            [0x20, 0x01, 0x02],
            [0x20, 0x01, 0x03],
            [0x20, 0x01, 0x05],
            [0x20, 0x01, 0x81],
            [0x20, 0x01, 0x82],
            [0x20, 0x01, 0x84],
            [0x20, 0x02],
            [0x20, 0x03],
        ])

    def test_get_blockchain_state_error_hash(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x01, 0x01]) +
            bytes.fromhex("11"*32),  # Response to get best_block
            bytes([0, 0, 0x01, 0x02]) +
            bytes.fromhex("22"*32),  # Response to get newest_valid_block
            bytes([0, 0, 0x01, 0x03]) +
            bytes.fromhex("33"*32),  # Response to get ancestor_block
            bytes([0, 0, 0x01, 0x05]) +
            bytes.fromhex("44"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0xAA]),  # Response to get updating.best_block
        ]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.get_blockchain_state()

        self.assert_exchange([
            [0x20, 0x01, 0x01],
            [0x20, 0x01, 0x02],
            [0x20, 0x01, 0x03],
            [0x20, 0x01, 0x05],
            [0x20, 0x01, 0x81],
        ])

    def test_get_blockchain_state_error_difficulty(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x01, 0x01]) +
            bytes.fromhex("11"*32),  # Response to get best_block
            bytes([0, 0, 0x01, 0x02]) +
            bytes.fromhex("22"*32),  # Response to get newest_valid_block
            bytes([0, 0, 0x01, 0x03]) +
            bytes.fromhex("33"*32),  # Response to get ancestor_block
            bytes([0, 0, 0x01, 0x05]) +
            bytes.fromhex("44"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x81]) +
            bytes.fromhex("55"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x82]) +
            bytes.fromhex("66"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x84]) +
            bytes.fromhex("77"*32),  # Response to get ancestor_receipts_root
            CommException("a-message"),
        ]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.get_blockchain_state()

        self.assert_exchange([
            [0x20, 0x01, 0x01],
            [0x20, 0x01, 0x02],
            [0x20, 0x01, 0x03],
            [0x20, 0x01, 0x05],
            [0x20, 0x01, 0x81],
            [0x20, 0x01, 0x82],
            [0x20, 0x01, 0x84],
            [0x20, 0x02],
        ])

    def test_get_blockchain_state_error_flags(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x01, 0x01]) +
            bytes.fromhex("11"*32),  # Response to get best_block
            bytes([0, 0, 0x01, 0x02]) +
            bytes.fromhex("22"*32),  # Response to get newest_valid_block
            bytes([0, 0, 0x01, 0x03]) +
            bytes.fromhex("33"*32),  # Response to get ancestor_block
            bytes([0, 0, 0x01, 0x05]) +
            bytes.fromhex("44"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x81]) +
            bytes.fromhex("55"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x82]) +
            bytes.fromhex("66"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x01, 0x84]) +
            bytes.fromhex("77"*32),  # Response to get ancestor_receipts_root
            bytes([0, 0, 0x02, 0xFF]),  # Response to get difficulty
            bytes([0, 0, 0x04]),  # Response to get flags
        ]

        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.get_blockchain_state()

        self.assert_exchange([
            [0x20, 0x01, 0x01],
            [0x20, 0x01, 0x02],
            [0x20, 0x01, 0x03],
            [0x20, 0x01, 0x05],
            [0x20, 0x01, 0x81],
            [0x20, 0x01, 0x82],
            [0x20, 0x01, 0x84],
            [0x20, 0x02],
            [0x20, 0x03],
        ])

    def test_reset_advance_blockchain_ok(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0x02]),  # Response
        ]
        self.assertTrue(self.hsm2dongle.reset_advance_blockchain())

        self.assert_exchange([
            [0x21, 0x01],
        ])

    def test_reset_advance_blockchain_invalid_response(self):
        self.dongle.exchange.side_effect = [
            bytes([0, 0, 0xAA]),  # Response
        ]
        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.reset_advance_blockchain()

        self.assert_exchange([
            [0x21, 0x01],
        ])

    def test_reset_advance_blockchain_exception(self):
        self.dongle.exchange.side_effect = [CommException("a-message")]
        with self.assertRaises(HSM2DongleError):
            self.hsm2dongle.reset_advance_blockchain()

        self.assert_exchange([
            [0x21, 0x01],
        ])
