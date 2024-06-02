import requests
import datetime
import os
import re
import textwrap
import time
import math

from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from bs4 import BeautifulSoup
import pdftotext

from anthropic import Anthropic
import google.generativeai as gemini
from notion_client import Client as notionClient
import asyncio
import aiohttp

from config import JP_CHUNK_LENGTH_LIMIT, EN_CHUNK_WORD_LIMIT, NOTION_BLOCK_REQUEST_LIMIT, USE_LLM_MODEL, IS_ASYNC_REQUEST
from prompts import SYSTEM_PROMPT, SUMMARY_PROMPT, GET_KEYWORDS_PROMPT

load_dotenv()


def isJapanese(text):
    return re.search(r'[ぁ-んァ-ヴ]', text)

url = input("Enter URL: ")

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36'
}

time_scrap_start = time.time()

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')
title = soup.title.text.replace(r'/', "|", 100)[0:min(50, len(soup.title.text))]

time_scrap_end = time.time()
print("Scraping Time: {} s".format( math.floor(time_scrap_end - time_scrap_start)))

if title == "403 Forbidden":
    print("403 Forbidden")
    exit()

word_list = []

# Youtube
if url.find("youtube.com") != -1:
    videoId = url.split("v=")[1]
    transcript = YouTubeTranscriptApi.get_transcript(videoId, languages=['en', 'ja'])
    for t in transcript:
        word_list.append(t['text'])

# study paper pdf in arxiv
elif url.find("arxiv.org/abs/") != -1:
    bin = requests.get(url.replace("abs", "pdf"), headers=headers)
    if os.path.isdir("bin") == False:
        os.mkdir("bin")
    pdfFileName = "bin/{}.pdf".format(title)
    with open(pdfFileName, "wb") as f:
        f.write(bin.content)
        f.close()
    open_pdf = open(pdfFileName, "rb")
    pdf = pdftotext.PDF(open_pdf)
    for page in pdf:
        word_list.append(page)

# Other Web Page
else:
    text = soup.get_text()
    # このサイトのメインコンテンツである記事部分のみを抜き出してください
    # サイドバー、広告、メニューに関するテキストは含めないでください
    text = textwrap.dedent(text)
    word_list = text.split()

def separate_chunk_by_length(word_list: list[str]) -> list[str]:
    chunkList = []
    chunkTmp = ""
    for word in word_list:
        chunkTmp += word + " "
        # ひらがなが含まれている
        if len(chunkTmp) > JP_CHUNK_LENGTH_LIMIT and isJapanese(chunkTmp) or len(chunkTmp.split(" ")) > EN_CHUNK_WORD_LIMIT and not isJapanese(chunkTmp):
            chunkList.append(chunkTmp)
            chunkTmp = ""
            continue
    chunkList.append(chunkTmp)
    return chunkList

chunked_transcript = separate_chunk_by_length(word_list)
print("---chunk length---", len(chunked_transcript))

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    max_retries=5,
)

gemini.configure(api_key=os.environ["GEMINI_API_KEY"])

notion = notionClient(auth=os.environ["NOTION_API_KEY"])

geminiSafetySettings = [
    {
        "category": "HARM_CATEGORY_DANGEROUS",
        "threshold": "BLOCK_NONE",
    },
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_NONE",
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_NONE",
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_NONE",
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_NONE",
    },
]

def requestLLM(systemPrompt: str, prompt: str) -> str:

    time_request_llm_start = time.time()
    if USE_LLM_MODEL == "claude-haiku":
        #### anthropic ###
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
        time_request_llm_end = time.time()
        wait_seconds = math.floor(time_request_llm_end - time_request_llm_start)
        print("requestLLM: {} s".format(wait_seconds), message.usage)
        return message.content[0].text

    elif USE_LLM_MODEL == "gemini-flash":
        #### gemini ####
        FLASH_TPM_LIMIT = 10_000_000
        model = gemini.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction=systemPrompt,
            safety_settings=geminiSafetySettings
        )
        response = model.generate_content(
            prompt,
        )
        time_request_llm_end = time.time()
        wait_seconds = math.floor(time_request_llm_end - time_request_llm_start)
        print("time {} s".format(wait_seconds))
        return response.text
    else:
        raise Exception("Invalid USE_LLM_MODEL")

async def requestLLMAsync(session, systemPrompt: str, prompt: str) -> str:
    if USE_LLM_MODEL == "gemini-flash":
        url = "https://generativelanguage.googleapis.com/v1beta/models/{}-latest:generateContent?key={}".format("gemini-1.5-flash", os.environ["GEMINI_API_KEY"])

        # https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/send-chat-prompts-gemini?hl=ja
        async with session.post(
            url,
            headers={
                "Content-Type": "application/json",
            },
            # "safetySettings": geminiSafetySettings
            json={
                "system_instruction": {
                    "parts": [{"text": systemPrompt}]
                },
                "contents": [
                    {"role": "user", "parts": [{"text": prompt}]}],
            }, 
        ) as response:
            responseJson = await response.json()
            return responseJson["candidates"][0]["content"]["parts"][0]["text"]
    else:
        raise Exception("Invalid USE_LLM_MODEL")

async def generateSummaryAsync(index: int, script: str, session) -> str:
    prompt = "以下の「{}」というタイトルの文章に対して日本語で要約を出力しなさい\n".format(title) + "```\n" + script + "\n```"
    ansText = await requestLLMAsync(session, SYSTEM_PROMPT, prompt)
    print("=== Progress: {}/{} ===\n".format(index+1, len(chunked_transcript)))
    print(ansText)

    # ひらがなが含まれているかどうかで判定
    retryCount = 3
    while not isJapanese(ansText) and retryCount > 0:
        prompt = "以下の文章を日本語に翻訳しなさい\n```\n" + script + "\n```"
        ansText = await requestLLMAsync(session, SYSTEM_PROMPT, prompt)
        retryCount -= 1
    return ansText

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

def convertNotionBlocks(textLines: list[str]) -> list[object]:
    blocks = []
    exceptTexts = ["", "リスト", "要約"]
    for line in textLines:
        # TODO: コードブロック対応

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
        # 2階層目
        elif line.startswith("  - "):
            if line.replace("  - ", "") in exceptTexts:
                continue
            block = createNotionBlock("bulleted_list_item", line.replace("  - ", ""))
            if "children" in blocks[-1]["bulleted_list_item"]:
                blocks[-1]["bulleted_list_item"]["children"].append(block)
            else:
                blocks[-1]["bulleted_list_item"]["children"] = [block]
        # 三階層目
        elif line.startswith("    - "):
            replacedLine = line.replace("    - ", "")
            if replacedLine in exceptTexts:
                continue
            block = createNotionBlock("bulleted_list_item", replacedLine)

            if not "children" in blocks[-1]["bulleted_list_item"]:
                blocks[-1]["bulleted_list_item"]["children"] = [block]
                continue
            lastBlockChildren = blocks[-1]["bulleted_list_item"]["children"]
            if "children" in lastBlockChildren[-1]["bulleted_list_item"]:
                lastBlockChildren[-1]["bulleted_list_item"]["children"].append(block)
            else:
               lastBlockChildren[-1]["bulleted_list_item"]["children"] = [block]
        # 一階層目
        else:
            if line.replace("- ", "") in exceptTexts:
                continue
            blocks.append(createNotionBlock("bulleted_list_item", line.replace("- ", "")))
    return blocks

if not os.path.isdir("summaries"):
    os.mkdir("summaries")


async def generateTaskWithSession(chunked_transcript: list[str]) -> list[str]:
    async with aiohttp.ClientSession() as session:
        tasks = [generateSummaryAsync(index, script, session) for index, script in enumerate(chunked_transcript)]
        responses = await asyncio.gather(*tasks)
    return responses

async def generateTaskFinishWithSession(summary: str) -> list[str]:
    async with aiohttp.ClientSession() as session:
        tasks = [
            requestLLMAsync(session, SUMMARY_PROMPT, SUMMARY_PROMPT + "\n\n```\n" + summary + "\n```"),
            requestLLMAsync(session, GET_KEYWORDS_PROMPT, GET_KEYWORDS_PROMPT + "\n```\n" + summary + "\n```")
        ]
        responses = await asyncio.gather(*tasks)
    return responses

with open("summaries/{} {}.md".format(title, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')), "w") as f:
    pageId = createNotionPage(title, url)
    f.write(url + "\n\n")

    # geminiの時は非同期リクエスト
    responseListLLM = []
    if IS_ASYNC_REQUEST:
        async_start = time.time()
        print("=== Start Async Request ===")
        responseListLLM = asyncio.run(
            generateTaskWithSession(chunked_transcript)
        )
        print("=== End Async Request ===")
        async_end = time.time()
        print("Async Time: {} s".format( math.floor(async_end - async_start)))
    else: 
        for i, script in enumerate(chunked_transcript):
            ansText = requestLLM(SYSTEM_PROMPT, "以下の「{}」というタイトルの文章に対して日本語で要約を出力しなさい\n".format(title) + "```\n" + script + "\n```")
            print("=== Progress: {}/{} ===\n".format(i+1, len(chunked_transcript)))
            print(ansText)
            
            # ひらがなが含まれているかどうかで判定
            retryCount = 3
            while not isJapanese(ansText) and retryCount > 0:
                ansText = requestLLM(SYSTEM_PROMPT, "以下の文章を日本語に翻訳しなさい\n```\n" + script + "\n```" )
                retryCount -= 1

            responseListLLM.append(ansText)

    
    try:
        time_notion_start = time.time()
        notionRequestChunk = [[]]
        print("converted blocks", end=" ")
        for i, responseText in enumerate(responseListLLM):
            f.write(responseText + "\n\n")
            responseTextLines = responseText.split("\n")
            blocks = convertNotionBlocks(responseTextLines)
            if len(notionRequestChunk[-1]) + len(blocks) > NOTION_BLOCK_REQUEST_LIMIT:
                notionRequestChunk.append(blocks)
            else:
                notionRequestChunk[-1] += blocks
            print(len(blocks), end=" ")
        print()
        generate_blocks_seconds = math.floor(time.time() - time_notion_start)
        print(
            "generate notion block chunk {} s, total {} chunks".format(generate_blocks_seconds, len(notionRequestChunk))
        )
        
        time_notion_api_start = time.time()
        for blocks in notionRequestChunk:
            appendRes = notion.blocks.children.append(
                block_id=pageId,
                children=blocks
            )
            print("success append", len(blocks), "blocks to notion page", end=" ")
        notion_api_request_seconds = math.floor(time.time() - time_notion_api_start)
        print("Notion API Insert Request Total Time:",notion_api_request_seconds, "s")
        
        ansTextSum = "\n".join(responseListLLM)
        summary = ""
        tags = []
        if IS_ASYNC_REQUEST:
            print("generating summary and keywords ...")
            [summary, keywordsText] = asyncio.run(
                generateTaskFinishWithSession(ansTextSum)
            )
            tags = keywordsText.split(",")[:5]
        else:
            print("generating summary ...")
            summary = requestLLM(SUMMARY_PROMPT, SUMMARY_PROMPT + "\n\n```\n" + ansTextSum + "\n```" )
            
            keywordsText = ""
            retryCount = 0
            print("generating keywords ...")
            while len(keywordsText.split(",")) <= 1 and retryCount < 3:
                keywordsText = requestLLM(GET_KEYWORDS_PROMPT, GET_KEYWORDS_PROMPT+"\n```\n" + ansTextSum + "\n```" )
                retryCount += 1
            
            if len(keywordsText.split(",")) > 1:
                tags = keywordsText.split(",")[:5]
        updatePageSummary(pageId, summary, tags)
        f.close()
    except Exception as e:
        print("error", str(e))
        res = notion.pages.update(
            page_id=pageId,
            body={
                "archived": True
            }
        )
        print(res)
        f.close()
        with open("error.log", "w") as log:
            log.write("{} {} Error: {}\n".format(
                    datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    url,
                    e
                )
            )
            log.close()