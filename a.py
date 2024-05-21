from youtube_transcript_api import YouTubeTranscriptApi
from anthropic import Anthropic
from bs4 import BeautifulSoup
import requests
import datetime
import os
import re
import textwrap
from dotenv import load_dotenv

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

systemPrompt = """
あなたは日本語で要約するエージェントです。与えられた文章がどんな言語でも日本語で要約を出力しなさい。出力フォーマットはマークダウンで主にネストされた箇条書きで文書として構造化して応答しなさい。参考文献をURLで引用している場合は箇条書き要素の文末に追加しなさい。出力例```# 要約タイトル\n- XXX\n   - XXXの詳細1\n   - XXXの詳細2\n - YYY\n```
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

with open("{} {}.md".format(title, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')), "w") as f:
    for script in chunked_transcript:
        ansText = requestLLM(script)
        # ひらがなが含まれているかどうかで判定
        print("re.match(r'[ぁ-ん]', ansText)----------", re.match(r'[ぁ-ん]', ansText))
        if not re.match(r'[ぁ-ん]', ansText):
            message = requestLLM("以下の文章を日本語に変換してください\n```" + ansText + "\n```" )

        f.write(ansText + "\n\n")
        print(script)
        print(ansText)
    f.close()




