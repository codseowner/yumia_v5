import MeCab

mecab = MeCab.Tagger()
result = mecab.parseToNode("私は日本語を勉強しています。")
taglist = []
while result:
    Part_of_speech = result.surface
    tag = result.feature.split(",")[0]
    if tag == "名詞":
        taglist.append(Part_of_speech)
    print(Part_of_speech)
    print(tag)
    result = result.next
print(taglist)