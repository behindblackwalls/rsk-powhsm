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

from unittest.mock import patch, call
from parameterized import parameterized
from ..test_hsm2dongle import TestHSM2DongleBase
from ledgerblue.commException import CommException

import logging

logging.disable(logging.CRITICAL)


class TestHSM2DongleAdvanceBlockchain(TestHSM2DongleBase):
    def setup_mocks(self,
                    mmplsize_mock,
                    get_cb_txn_mock,
                    cb_txn_get_hash_mock,
                    gbh_mock):
        mmplsize_mock.side_effect = lambda h: len(h)//8
        get_cb_txn_mock.side_effect = lambda h: {"cb_txn": h}
        cb_txn_get_hash_mock.side_effect = lambda h: \
            (bytes([len(h["cb_txn"])//5])*4).hex()
        gbh_mock.return_value = "00"

    @parameterized.expand([
        ("partial_v2.0.x", 0x05, 2),
        ("total_v2.0.x", 0x06, 1),
        ("partial_v2.1.x", 0x05, 2),
        ("total_v2.1.x", 0x06, 1),
    ])
    @patch("ledger.hsm2dongle.get_block_hash")
    @patch("ledger.hsm2dongle.coinbase_tx_get_hash")
    @patch("ledger.hsm2dongle.get_coinbase_txn")
    @patch("ledger.hsm2dongle.rlp_mm_payload_size")
    def test_advance_blockchain_ok(
        self,
        _,
        device_response,
        expected_response,
        mmplsize_mock,
        get_cb_txn_mock,
        cb_txn_get_hash_mock,
        gbh_mock,
    ):
        self.setup_mocks(mmplsize_mock,
                         get_cb_txn_mock,
                         cb_txn_get_hash_mock,
                         gbh_mock)
        brothers_spec = [
            # (brother list of brother bytes, chunk size)
            ([self.buf(190), self.buf(100)], 90),
            None,  # 2nd block has no brothers
            ([self.buf(130)], 60),
        ]
        blocks_spec = [
            # (block bytes, chunk size, brothers)
            (self.buf(300), 80, brothers_spec[0]),
            (self.buf(250), 100, brothers_spec[1]),
            (self.buf(140), 50, brothers_spec[2]),
        ]

        self.dongle.exchange.side_effect = [
            bs for excs in map(self.spec_to_exchange, blocks_spec)
            for bs in excs
        ] + [bytes([0, 0, device_response])]  # Success response

        blocks_hex = list(map(lambda bs: bs[0].hex(), blocks_spec))
        brothers_list = list(map(
            lambda bs: list(map(
                lambda b: b.hex(), bs[0])) if bs else [],
            brothers_spec))
        self.assertEqual(
            (True, expected_response),
            self.hsm2dongle.advance_blockchain(blocks_hex, brothers_list),
        )

        self.assert_exchange([
            [0x10, 0x02, 0x00, 0x00, 0x00, 0x03],  # Init, 3 blocks
            [0x10, 0x03, 0x00, 0x4B] +
            [0x78, 0x78, 0x78, 0x78],  # Blk #1 meta
            [0x10, 0x04] + list(blocks_spec[0][0][80*0:80*1]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*1:80*2]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*2:80*3]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*3:80*4]),  # Blk #1 chunk
            [0x10, 0x07, 0x02],  # Blk #1 brother count
            [0x10, 0x08, 0x00, 0x2f, 0x4c, 0x4c, 0x4c, 0x4c],  # Blk #1 bro #1 meta
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*0:90*1]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*1:90*2]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*2:90*3]),  # Blk #1 bro #1 chunk
            [0x10, 0x08, 0x00, 0x19, 0x28, 0x28, 0x28, 0x28],  # Blk #1 bro #2 meta
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*0:90*1]),  # Blk #1 bro #2 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*1:90*2]),  # Blk #1 bro #2 chunk
            [0x10, 0x03, 0x00, 0x3E] +
            [0x64, 0x64, 0x64, 0x64],  # Blk #2 meta
            [0x10, 0x04] + list(blocks_spec[1][0][100*0:100*1]),  # Blk #2 chunk
            [0x10, 0x04] + list(blocks_spec[1][0][100*1:100*2]),  # Blk #2 chunk
            [0x10, 0x04] + list(blocks_spec[1][0][100*2:100*3]),  # Blk #2 chunk
            [0x10, 0x07, 0x00],  # Blk #2 brother count
            [0x10, 0x03, 0x00, 0x23] +
            [0x38, 0x38, 0x38, 0x38],  # Blk #3 meta
            [0x10, 0x04] + list(blocks_spec[2][0][50*0:50*1]),  # Blk #3 chunk
            [0x10, 0x04] + list(blocks_spec[2][0][50*1:50*2]),  # Blk #3 chunk
            [0x10, 0x04] + list(blocks_spec[2][0][50*2:50*3]),  # Blk #3 chunk
            [0x10, 0x07, 0x01],  # Blk #3 brother count
            [0x10, 0x08, 0x00, 0x20, 0x34, 0x34, 0x34, 0x34],  # Blk #3 bro #1 meta
            [0x10, 0x09] + list(brothers_spec[2][0][0][60*0:60*1]),  # Blk #3 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[2][0][0][60*1:60*2]),  # Blk #3 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[2][0][0][60*2:60*3]),  # Blk #3 bro #1 chunk
        ])

    @parameterized.expand(TestHSM2DongleBase.CHUNK_ERROR_MAPPINGS)
    @patch("ledger.hsm2dongle.get_block_hash")
    @patch("ledger.hsm2dongle.coinbase_tx_get_hash")
    @patch("ledger.hsm2dongle.get_coinbase_txn")
    @patch("ledger.hsm2dongle.rlp_mm_payload_size")
    def test_advance_blockchain_chunk_error_result(
        self,
        _,
        error_code,
        response,
        mmplsize_mock,
        get_cb_txn_mock,
        cb_txn_get_hash_mock,
        gbh_mock,
    ):
        self.setup_mocks(mmplsize_mock,
                         get_cb_txn_mock,
                         cb_txn_get_hash_mock,
                         gbh_mock)
        brothers_spec = [
            # (brother list of brother bytes, chunk size)
            ([self.buf(190), self.buf(100)], 90),
            None,  # 2nd block has no brothers
            ([self.buf(130)], 60),
        ]
        blocks_spec = [
            # (block bytes, chunk size, brothers)
            (self.buf(300), 80, brothers_spec[0]),
            (self.buf(250), 100, brothers_spec[1]),
            (self.buf(140), 50, brothers_spec[2]),
        ]

        side_effect = [
            bs for excs in map(self.spec_to_exchange, blocks_spec)
            for bs in excs
        ]

        # Make the second chunk of the second block fail
        # First block meta & chunks & bro metas & chunks
        # + second block meta & first & second chunk
        exchange_index = (
            (1 + 300//80 + 1) + 1 + (1 + 190//90 + 1) + (1 + 100//90 + 1) + 3
        )

        if type(error_code) == bytes:
            side_effect[exchange_index] = error_code
        else:
            side_effect[exchange_index] = CommException("a-message", error_code)
        side_effect = side_effect[:exchange_index + 1]
        self.dongle.exchange.side_effect = side_effect

        blocks_hex = list(map(lambda bs: bs[0].hex(), blocks_spec))
        brothers_list = list(map(
            lambda bs: list(map(
                lambda b: b.hex(), bs[0])) if bs else [],
            brothers_spec))

        self.assertEqual(
            (False, response),
            self.hsm2dongle.advance_blockchain(blocks_hex, brothers_list),
        )

        self.assert_exchange([
            [0x10, 0x02, 0x00, 0x00, 0x00, 0x03],  # Init, 3 blocks
            [0x10, 0x03, 0x00, 0x4B] +
            [0x78, 0x78, 0x78, 0x78],  # Blk #1 meta
            [0x10, 0x04] + list(blocks_spec[0][0][80*0:80*1]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*1:80*2]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*2:80*3]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*3:80*4]),  # Blk #1 chunk
            [0x10, 0x07, 0x02],  # Blk #1 brother count
            [0x10, 0x08, 0x00, 0x2f, 0x4c, 0x4c, 0x4c, 0x4c],  # Blk #1 bro #1 meta
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*0:90*1]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*1:90*2]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*2:90*3]),  # Blk #1 bro #1 chunk
            [0x10, 0x08, 0x00, 0x19, 0x28, 0x28, 0x28, 0x28],  # Blk #1 bro #2 meta
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*0:90*1]),  # Blk #1 bro #2 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*1:90*2]),  # Blk #1 bro #2 chunk
            [0x10, 0x03, 0x00, 0x3E] +
            [0x64, 0x64, 0x64, 0x64],  # Blk #2 meta
            [0x10, 0x04] + list(blocks_spec[1][0][100*0:100*1]),  # Blk #2 chunk
            [0x10, 0x04] + list(blocks_spec[1][0][100*1:100*2]),  # Blk #2 chunk
        ])

    @parameterized.expand([
        ("prot_invalid", 0x6B87, -3),
        ("unexpected", 0x6BFF, -10),
        ("error_response", bytes([0, 0, 0xFF]), -10),
    ])
    @patch("ledger.hsm2dongle.get_block_hash")
    @patch("ledger.hsm2dongle.coinbase_tx_get_hash")
    @patch("ledger.hsm2dongle.get_coinbase_txn")
    @patch("ledger.hsm2dongle.rlp_mm_payload_size")
    def test_advance_blockchain_metadata_error_result(
        self,
        _,
        error_code,
        response,
        mmplsize_mock,
        get_cb_txn_mock,
        cb_txn_get_hash_mock,
        gbh_mock,
    ):
        self.setup_mocks(mmplsize_mock,
                         get_cb_txn_mock,
                         cb_txn_get_hash_mock,
                         gbh_mock)
        brothers_spec = [
            # (brother list of brother bytes, chunk size)
            ([self.buf(190), self.buf(100)], 90),
            None,  # 2nd block has no brothers
            ([self.buf(130)], 60),
        ]
        blocks_spec = [
            # (block bytes, chunk size, brothers)
            (self.buf(300), 80, brothers_spec[0]),
            (self.buf(250), 100, brothers_spec[1]),
            (self.buf(140), 50, brothers_spec[2]),
        ]

        side_effect = [
            bs for excs in map(self.spec_to_exchange, blocks_spec)
            for bs in excs
        ]

        # Make the metadata of the third block fail
        # First block meta & chunks & bro metas & chunks
        # + second block meta & chunks & bro meta
        # + third block meta
        exchange_index = (
            (1 + 300//80 + 1) + 1 + (1 + 190//90 + 1) + (1 + 100//90 + 1) +
            (1 + 250//100 + 1) + 1 +
            1
        )

        if type(error_code) == bytes:
            side_effect[exchange_index] = error_code
        else:
            side_effect[exchange_index] = CommException("a-message", error_code)
        side_effect = side_effect[:exchange_index + 1]
        self.dongle.exchange.side_effect = side_effect

        blocks_hex = list(map(lambda bs: bs[0].hex(), blocks_spec))

        brothers_list = list(map(
            lambda bs: list(map(
                lambda b: b.hex(), bs[0])) if bs else [],
            brothers_spec))

        self.assertEqual(
            (False, response),
            self.hsm2dongle.advance_blockchain(blocks_hex, brothers_list),
        )

        self.assert_exchange([
            [0x10, 0x02, 0x00, 0x00, 0x00, 0x03],  # Init, 3 blocks
            [0x10, 0x03, 0x00, 0x4B] +
            [0x78, 0x78, 0x78, 0x78],  # Blk #1 meta
            [0x10, 0x04] + list(blocks_spec[0][0][80*0:80*1]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*1:80*2]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*2:80*3]),  # Blk #1 chunk
            [0x10, 0x04] + list(blocks_spec[0][0][80*3:80*4]),  # Blk #1 chunk
            [0x10, 0x07, 0x02],  # Blk #1 brother count
            [0x10, 0x08, 0x00, 0x2f, 0x4c, 0x4c, 0x4c, 0x4c],  # Blk #1 bro #1 meta
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*0:90*1]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*1:90*2]),  # Blk #1 bro #1 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][0][90*2:90*3]),  # Blk #1 bro #1 chunk
            [0x10, 0x08, 0x00, 0x19, 0x28, 0x28, 0x28, 0x28],  # Blk #1 bro #2 meta
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*0:90*1]),  # Blk #1 bro #2 chunk
            [0x10, 0x09] + list(brothers_spec[0][0][1][90*1:90*2]),  # Blk #1 bro #2 chunk
            [0x10, 0x03, 0x00, 0x3E] +
            [0x64, 0x64, 0x64, 0x64],  # Blk #2 meta
            [0x10, 0x04] + list(blocks_spec[1][0][100*0:100*1]),  # Blk #2 chunk
            [0x10, 0x04] + list(blocks_spec[1][0][100*1:100*2]),  # Blk #2 chunk
            [0x10, 0x04] + list(blocks_spec[1][0][100*2:100*3]),  # Blk #2 chunk
            [0x10, 0x07, 0x00],  # Blk #2 brother count
            [0x10, 0x03, 0x00, 0x23] +
            [0x38, 0x38, 0x38, 0x38],  # Blk #3 meta
        ])

    @patch("ledger.hsm2dongle.rlp_mm_payload_size")
    def test_advance_blockchain_metadata_error_generating(self, mmplsize_mock):
        mmplsize_mock.side_effect = ValueError()
        self.dongle.exchange.side_effect = [bytes([0, 0, 0x03])]

        self.assertEqual(
            (False, -2),
            self.hsm2dongle.advance_blockchain(["first-block", "second-block"],
                                               [[], []]),
        )

        self.assert_exchange([
            [0x10, 0x02, 0x00, 0x00, 0x00, 0x02],  # Init, 2 blocks
        ])
        self.assertEqual([call("first-block")], mmplsize_mock.call_args_list)

    @parameterized.expand([
        ("prot_invalid", CommException("a-message", 0x6B87), -1),
        ("unexpected", CommException("a-message", 0x6BFF), -10),
        ("invalid_response", bytes([0, 0, 0xFF]), -10),
    ])
    def test_advance_blockchain_init_error(self, _, error, response):
        self.dongle.exchange.side_effect = [error]

        self.assertEqual(
            (False, response),
            self.hsm2dongle.advance_blockchain(["first-block", "second-block"],
                                               [[], []]),
        )

        self.assert_exchange([
            [0x10, 0x02, 0x00, 0x00, 0x00, 0x02],  # Init, 2 blocks
        ])
