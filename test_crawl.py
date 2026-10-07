from crawl import clean_text, extract_id, is_truncated, split_confessions
from crawl_v2 import key
from update import find_new

assert clean_text("hỏi ạ\n#UITconfessions: bit.ly/UITConfessions Ẩn bớt") == "hỏi ạ"
assert clean_text("hỏi ạ #UITconfessions: bit.ly/UITConfession") == "hỏi ạ"
assert clean_text("a #UITconfessions Ẩn bớt") == "a"
assert clean_text("câu hỏi. ------ Ẩn bớt") == "câu hỏi."
assert clean_text("one\n-----------\ntwo\n.\nthree") == "one\ntwo\nthree"
assert clean_text("Thích mình thích chia sẻ") == "Thích mình thích chia sẻ"   # real words stay
assert clean_text("nội dung dài… Xem thêm") == "nội dung dài" and is_truncated("x… Xem thêm")
assert not is_truncated("full text Ẩn bớt")

assert extract_id("#UIT12345 hello") == "12345"
assert extract_id("#678 xin chào") == "678"
assert extract_id("no number", "https://facebook.com/UITconfess/posts/pfbid0abc") == "pfbid0abc"
assert extract_id("no number", "https://facebook.com/x") is None
assert split_confessions("#3686\nfirst\n-------------\n#3687\nsecond") == [("3686", "first"), ("3687", "second")]
assert split_confessions("no id here") == []
assert split_confessions("#1: first one\n#2: second one") == [("1", "first one"), ("2", "second one")]
assert split_confessions("#5\nreply to #3 here: ok") == [("5", "reply to #3 here: ok")]

# update.find_new: newest-first feed; stops after a run of already-known posts; ignores repeats
known = {key(str(i), f"post {i}") for i in range(1, 40)}
feed = [("42", "post 42"), ("41", "post 41"), ("41", "post 41"), ("40", "post 40")] + [(str(i), f"post {i}") for i in range(39, 0, -1)]
new, reached = find_new(feed, known, stop_after=5)
assert new == [("42", "post 42"), ("41", "post 41"), ("40", "post 40")] and reached
assert find_new([("5", "post 5")] * 3, known, stop_after=5) == ([], False)          # fewer known posts than the stop run
assert find_new([("77", "brand new")], known)[0] == [("77", "brand new")]
assert key("9", "post   9 Ẩn bớt") == key("9", "post 9")                           # page furniture does not make a post "new"
print("ok")
