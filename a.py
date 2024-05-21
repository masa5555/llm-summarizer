from youtube_transcript_api import YouTubeTranscriptApi
from anthropic import Anthropic
from bs4 import BeautifulSoup
import requests
import datetime
import os
import re
from dotenv import load_dotenv

load_dotenv()

url = input("Enter URL: ")
response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')
title = soup.title.text.replace(r'/', "|", 100)

chunked_transcript = []
chunk = ""

if url.find("youtube.com") != -1:
    videoId = url.split("v=")[1]
    transcript = YouTubeTranscriptApi.get_transcript(videoId)

    for i in transcript:
        chunk += i['text'] + " "
        if len(chunk.split(" ")) > 1000:
            chunked_transcript.append(chunk)
            chunk = ""
    chunked_transcript.append(chunk)


client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

systemPrompt = """
あなたは日本語で要約するエージェントです。与えられた文章がどんな言語でも日本語で要約を出力しなさい。出力フォーマットはネストされた箇条書きで構造化し、それ以外の前後のつなぎ無しで応答して下さい。出力例```- XXX\n   - XXXの詳細1\n   - XXXの詳細2\n - YYY\n```
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
    return message.content[0].text

with open("{} {}.txt".format(title, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')), "w") as f:
    for chunk in chunked_transcript:
        ansText = requestLLM(chunk)
        # ひらがなが含まれているかどうかで判定
        if not re.match(r'[ぁ-ん]', ansText):
            message = requestLLM("以下の文章を日本語に変換してください\n```" + ansText + "\n```" )

        f.write(ansText + "\n\n")
        print(chunk, ansText)
    f.close()




