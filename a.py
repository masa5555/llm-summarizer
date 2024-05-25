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

chunked_transcript = []
CHUNK_WORD_LIMIT = 1000

if url.find("youtube.com") != -1:
    videoId = url.split("v=")[1]
    transcript = YouTubeTranscriptApi.get_transcript(videoId)

    chunk = ""
    for i in transcript:
        chunk += i['text'] + " "
        if len(chunk.split(" ")) > CHUNK_WORD_LIMIT:
            chunked_transcript.append(chunk)
            chunk = ""
    chunked_transcript.append(chunk)
else:
    text = soup.get_text()
    text = textwrap.dedent(text)
    word_list = text.split()
    chunk = ""
    for word in word_list:
        chunk += word + " "
        if len(chunk.split(" ")) > CHUNK_WORD_LIMIT:
            chunked_transcript.append(chunk)
            chunk = ""
    chunked_transcript.append(chunk)

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

notion = notionClient(auth=os.environ["NOTION_API_KEY"])

systemPrompt = """
・あなたは日本語で要約するエージェントです。
・与えられた文章がどんな言語でも日本語で要約を出力しなさい。
　・文書として情報量のない言葉は要約の中に絶対に入れないでください。
　・参考文献をURLで引用している場合は箇条書き要素の文末に追加しなさい。
・出力フォーマットはマークダウンで主にネストされた箇条書きで文書として構造化して応答しなさい。出力例```# 要約した内容が一言でわかるタイトル\n- XXX\n   - XXXの詳細1\n   - XXXの詳細2\n - YYY\n```
"""

def requestLLM(prompt):
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
    print("------------------Usage--------------", message.usage)
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
        ansText = requestLLM(script)
        ansTextSum += ansText
        # ひらがなが含まれているかどうかで判定
        print("re.match(r'[ぁ-ん]', ansText)----------", re.match(r'[ぁ-ん]', ansText))
        if not re.match(r'[ぁ-ん]', ansText):
            message = requestLLM("以下の文章を日本語に変換してください\n```" + ansText + "\n```" )

        f.write(ansText + "\n\n")
        ansTextLines = ansText.split("\n")
        blocks = []
        for line in ansTextLines:
            if line.startswith("# "):
                blocks.append(createNotionBlock("heading_1", line.replace("# ", "")))
            elif line.startswith("## "):
                blocks.append(createNotionBlock("heading_2", line.replace("## ", "")))
            elif line.startswith("### "):
                blocks.append(createNotionBlock("heading_3", line.replace("### ", "")))
            else:
                blocks.append(createNotionBlock("bulleted_list_item", line))
        # print(blocks)

        notion.blocks.children.append(
            block_id=pageId,
            children=blocks
        )
    summary = requestLLM("以下の文章を1文かつ100文字以内で要約し、その要約1文のみ短答してください。それ以外の文字は出力に含めないでください。\n```" + ansTextSum + "\n```" )
    tagsText = requestLLM("以下の文章から主要なキーワードを重要度が高い順に5個抜き出し、カンマ区切りでキーワードのリストだけを出力して下さい\n```" + ansTextSum + "\n```" )
    tags = []
    if len(tagsText.split(",")) > 0:
        tags = tagsText.split(",")[:5]
    updatePageSummary(pageId, summary, tags)
    f.close()
