from crawl import clean_text, extract_id, is_truncated, split_confessions

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
print("ok")
