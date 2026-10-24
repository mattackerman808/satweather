from satweather.signal import sample

STATS = {"ccsds_conv_concat_decoder": {"deframer_lock": True, "rs_avg": 0, "viterbi_ber": 0.0439453125, "viterbi_lock": 1},
         "psk_demod": {"freq": 231.8182830810547, "peak_snr": 4.181001663208008, "snr": 3.014723062515259}}


def test_sample_from_satdump_stats():
    s = sample(STATS)
    assert s["ok"] and s["deframer_lock"] is True and s["viterbi_lock"] is True
    assert round(s["snr"], 2) == 3.01 and round(s["peak_snr"], 2) == 4.18 and round(s["freq_hz"]) == 232
    assert s["viterbi_ber"] == 0.0439453125 and s["rs_avg"] == 0 and s["raw"] == STATS


def test_no_answer_and_partial_stats():
    assert sample(None) == {"ok": False, "snr": None, "peak_snr": None, "freq_hz": None, "viterbi_ber": None,
                            "viterbi_lock": None, "deframer_lock": None, "rs_avg": None, "raw": None}
    s = sample({"psk_demod": {"snr": "nan", "peak_snr": 5}})  # decoder not started yet
    assert s["ok"] and s["snr"] is None and s["peak_snr"] == 5 and s["deframer_lock"] is None
