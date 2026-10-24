"""Receiver signal samples: SatDump's live stats, polled by satweather-signal.

One row per poll. ok is false when the receiver's stats server did not
answer (rtl down, SatDump restarting), so outages show as gaps with a
reason rather than as missing rows. raw keeps the whole JSON, in case a
later SatDump reports more.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-24
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE signal_sample (
            t             timestamptz PRIMARY KEY DEFAULT now(),
            ok            boolean NOT NULL,       -- the stats server answered
            snr           real,                   -- dB, demodulator
            peak_snr      real,
            freq_hz       real,                   -- carrier offset the demodulator is tracking
            viterbi_ber   real,                   -- bit error rate before error correction
            viterbi_lock  boolean,
            deframer_lock boolean,                -- CADU frames in sync
            rs_avg        real,                   -- Reed-Solomon corrections per frame (avg of 4)
            raw           jsonb
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS signal_sample")
