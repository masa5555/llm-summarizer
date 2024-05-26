from youtube_transcript_api import YouTubeTranscriptApi
from anthropic import Anthropic
from bs4 import BeautifulSoup
import requests
import datetime
import os
import re
import textwrap
from dotenv import load_dotenv
from notion_client import Client as notionClient

load_dotenv()
def isJapanese(text):
    return re.search(r'[ぁ-んァ-ヴ]', text)

url = input("Enter URL: ")

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36'
}

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')
title = soup.title.text.replace(r'/', "|", 100)

if title == "403 Forbidden":
    print("403 Forbidden")
    exit()

word_list = []
chunked_transcript = []
JP_CHUNK_LENGTH_LIMIT = 2000
EN_CHUNK_WORD_LIMIT = 1000

if url.find("youtube.com") != -1:
    videoId = url.split("v=")[1]
    transcript = YouTubeTranscriptApi.get_transcript(videoId, languages=['en', 'ja'])
    for t in transcript:
        word_list.append(t['text'])
else:
    text = soup.get_text()
    text = textwrap.dedent(text)
    word_list = text.split()

chunk = ""
for word in word_list:
    chunk += word + " "
    # ひらがなが含まれている
    if len(chunk) > JP_CHUNK_LENGTH_LIMIT and isJapanese(chunk):
        chunked_transcript.append(chunk)
        chunk = ""
        continue
    # 英語の時
    if len(chunk.split(" ")) > EN_CHUNK_WORD_LIMIT and not isJapanese(chunk):
        chunked_transcript.append(chunk)
        chunk = ""
        continue
chunked_transcript.append(chunk)
print("---chunk length---", len(chunked_transcript))

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

notion = notionClient(auth=os.environ["NOTION_API_KEY"])

systemPrompt = """
・あなたは日本語で要約するエージェントです。
・与えられた文章がどんな言語でも日本語で要約を出力しなさい。
　・文書として情報量のない言葉は要約の中に絶対に入れないでください。
　・参考文献をURLで引用している場合は箇条書き要素の文末に追加しなさい。
・出力フォーマットはマークダウンで主にネストされた箇条書きで文書として構造化して応答しなさい。出力例```# 要約した内容が一言でわかるタイトル\n - XXX\n  - XXXの詳細1\n  - XXXの詳細2\n- YYY\n```
"""

def requestLLM(systemPrompt, prompt):
    message = client.messages.create(
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": prompt,
            },
        ],
        system=systemPrompt,
        model="claude-3-haiku-20240307",
    )
    print(message.usage)
    return message.content[0].text

def createNotionPage(title, url):
    response = notion.pages.create(
        parent={"database_id": os.getenv("NOTION_DATABASE_ID")},
        properties={
            "Title": {
                "title": [
                    {
                        "text": {
                            "content": title,
                            "link": {
                                "url": url
                            }
                        }
                    }
                ]
            },
            "Summary": {
                "rich_text": [
                    {
                        "text": {
                            "content": ""
                        }
                    }
                ]
            }
        }
    )
    pageId = response["id"]
    print("pageId", pageId)
    notion.blocks.children.append(
        block_id=pageId,
        children=[
            {
                "object": "block",
                "type": "table_of_contents",
                "table_of_contents": {}
            }
        ]
    )
    return pageId

def createNotionBlock(type, text):
    block = {
        "object": "block",
        "type": type,
        type: {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": text
                    }
                }
            ]
        }
    }
    return block

def updatePageSummary(pageId, summary, tags):
    tagBlocks = [{"name": tag} for tag in tags]
    notion.pages.update(
        page_id=pageId,
        properties={
            "Summary": {
                "rich_text": [
                    {
                        "text": {
                            "content": summary
                        }
                    }
                ]
            },
            "Tags": {
                "multi_select": tagBlocks
            }
        }
    )
    return

with open("summaries/{} {}.md".format(title, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')), "w") as f:
    f.write(url + "\n\n")
    pageId = createNotionPage(title, url)
    ansTextSum = ""
    for script in chunked_transcript:
        ansText = requestLLM(systemPrompt, "以下の文章に対して日本語で回答を出力しなさい\n```" + script + "\n```")
        
        # ひらがなが含まれているかどうかで判定
        print("---isJapanase---", isJapanese(ansText))
        retryCount = 3
        while not isJapanese(ansText) and retryCount > 0:
            ansText = requestLLM(systemPrompt, "以下の文章を日本語に翻訳しなさい\n```" + script + "\n```" )
            retryCount -= 1
            if retryCount == 0:
                break
        ansTextSum += ansText

        f.write(ansText + "\n\n")
        ansTextLines = ansText.split("\n")
        blocks = []
        exceptTexts = ["", "リスト"]
        for line in ansTextLines:
            if line.startswith("# "):
                if line.replace("# ", "") in exceptTexts:
                    continue
                blocks.append(createNotionBlock("heading_1", line.replace("# ", "")))
            elif line.startswith("## "):
                if line.replace("## ", "") in exceptTexts:
                    continue
                blocks.append(createNotionBlock("heading_2", line.replace("## ", "")))
            elif line.startswith("### "):
                if line.replace("### ", "") in exceptTexts:
                    continue
                blocks.append(createNotionBlock("heading_3", line.replace("### ", "")))
            elif line.startswith("  - "):
                if line.replace("  - ", "") in exceptTexts:
                    continue
                block = createNotionBlock("bulleted_list_item", line.replace("  - ", ""))
                blocks[-1]["bulleted_list_item"]["children"] = [block]
            else:
                if line.replace("- ", "") in exceptTexts:
                    continue
                blocks.append(createNotionBlock("bulleted_list_item", line.replace("- ", "")))

        # print(blocks)

        notion.blocks.children.append(
            block_id=pageId,
            children=blocks
        )
    summary = requestLLM("文章を1文かつ100文字以内で要約し、その要約1文のみ短答してください。それ以外の文字は出力に含めないでください。・要約にはどんな意図でこの文章が作られたかを含めてください。", "以下の文章を1文かつ100文字以内で要約し、その要約1文のみ短答してください。それ以外の文字は出力に含めないでください。\n```" + ansTextSum + "\n```" )
    tagsText = requestLLM("以下の文章から主要なキーワードを重要度が高い順に5個抜き出し、カンマ区切りでキーワードのリストだけを出力して下さい", "以下の文章から主要なキーワードを重要度が高い順に5個抜き出し、半角カンマ区切りで連結されたキーワードのリストだけを出力して下さい"+"\n```" + ansTextSum + "\n```" )
    
    if len(tagsText.split(",")) <= 1:
        tagsText = requestLLM("以下の文章を象徴するキーワードを5個抜き出し、カンマ区切りでキーワードのリストだけを出力して下さい", "以下の文章から主要なキーワードを重要度が高い順に5個抜き出し、半角カンマ区切りで連結されたキーワードのリストだけを出力して下さい"+"\n```" + ansTextSum + "\n```" )

    if len(tagsText.split(",")) > 1:
        tags = tagsText.split(",")[:5]
    else:
        tags = []
    updatePageSummary(pageId, summary, tags)
    f.close()
