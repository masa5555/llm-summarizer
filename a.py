from youtube_transcript_api import YouTubeTranscriptApi
from anthropic import Anthropic
from bs4 import BeautifulSoup
import requests

videoId = input("Enter the video ID: ")

url = "https://www.youtube.com/watch?v={}".format(videoId)
response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')
print(soup.title.text)
title = soup.title.text

transcript = YouTubeTranscriptApi.get_transcript(videoId)
print(transcript)

chunked_transcript = []
chunk = ""
for i in transcript:
  chunk += i['text'] + " "
  if len(chunk.split(" ")) > 1000:
    chunked_transcript.append(chunk)
    chunk = ""
chunked_transcript.append(chunk)

client = Anthropic(
    # This is the default and can be omitted
    api_key="xxxxx",
)

translated = []

for chunk in chunked_transcript:
    message = client.messages.create(
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": chunk,
            },
        ],
        retry_on_rate_limit=True,
        system="あなたは要約エージェントです。与えられた文章について日本語で要約してください。出力フォーマットはネストされた箇条書きで構造化し、それ以外の前後のつなぎ無しで応答して下さい。出力例```- 要点1\n   - 要点1-詳細A\n   - 要点1-詳細B\n - 要点2\n```",
        model="claude-3-haiku-20240307",
    )
    translated.append(message.content[0].text)
    print(chunk, message.content[0].text)

with open("{}.txt".format(title), "w") as f:
    for chunk in translated:
        f.write(chunk + "\n\n")




