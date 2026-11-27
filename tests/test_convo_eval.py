"""
The 10,000-conversation eval: the generator is deterministic and the graders
say what they mean (evals/convos.py, evals/spoken.py).
"""
from evals import convos, spoken


def test_ten_thousand_conversations_deterministic():
    a = convos.scenarios()
    assert len(a) == 5000 and len(list(convos.conversations())) == 10000
    assert convos.scenarios(50) == a[:50]          # same seed, same conversations
    assert {s["tier"] for s in a} == {"green", "amber", "red"}
    assert len({s["intent"] for s in a}) >= 45


def test_spoken_copy_reads_like_speech_recognition():
    for s in convos.scenarios(300):
        for v in s["voice"]:
            assert v == v.lower() and "?" not in v and "!" not in v


def test_sample_covers_every_intent():
    scs = convos.scenarios()
    picked = convos.sample(scs, 10)
    assert {s["intent"] for s in picked} == {s["intent"] for s in scs}


def test_speakable_check():
    assert spoken.speakable_as_written("Saturn at 21° is loud.", "voice")[0] is False
    assert spoken.speakable_as_written("Your AC is Leo.", "voice")[0] is False
    assert spoken.speakable_as_written("Saturn at 21° is loud.", "text")[0] is True
    assert spoken.speakable_as_written("Saturn is across from your Moon until Thursday.", "voice")[0]


def test_ear_length():
    long = " ".join(["word"] * 30) + "."
    assert spoken.ear_length_sentences(long, "voice")[0] is False
    assert spoken.ear_length_sentences(long, "text")[0] is True


def test_one_question_ignores_a_beat():
    assert spoken.one_question("Honestly? It's a quiet day. Want the deep cut?")[0]
    assert not spoken.one_question("Is it him? Or the job?")[0]


def test_verdict_check_ignores_negation():
    assert spoken.withholds_verdict("No transit is a reason to do it, wait, or not.")[0]
    assert not spoken.withholds_verdict("Honestly? Just do it.")[0]


def test_identity_question_and_answer():
    assert spoken.ASKS_IDENTITY.search("wait is this a real person")
    assert spoken.ASKS_IDENTITY.search("are you a psychic or what")
    assert not spoken.ASKS_IDENTITY.search("do you actually see the future")
    assert spoken.says_its_ai("I'm My Sign, an AI that reads your chart.")[0]


def test_psychic_claims():
    assert not spoken.no_psychic_claims("The ball shows me a tall stranger.")[0]
    assert spoken.no_psychic_claims("Your real sky says Venus is helping you.")[0]


def test_no_repeats_and_rhythm():
    same = "Mars is clashing with your Moon until Thursday, so go easy."
    assert not spoken.no_repeats([same, "ok.", same])[0]
    assert not spoken.question_rhythm(["a?", "b?", "c?"])[0]
    assert spoken.question_rhythm(["a?", "b?", "c."])[0]


def test_parity():
    ok = {"one_question": {"ok": True}}
    bad = {"one_question": {"ok": False}}
    chat = [{"text": "Short answer here. Want more?", "checks": ok}]
    assert spoken.parity(chat, [{"text": "Short answer here. Want more?", "checks": ok}])[0]
    assert not spoken.parity(chat, [{"text": "Two? Questions?", "checks": bad}])[0]
    assert not spoken.parity(chat, [{"text": "word " * 60, "checks": ok}])[0]
