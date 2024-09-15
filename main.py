import json
import logging
import os
import zoneinfo
from pprint import pformat

import feedparser
import google.cloud.firestore
import google.cloud.logging
import requests
from googleapiclient.discovery import build

# 標準 Logger の設定
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger()

# Cloud Logging ハンドラを logger に接続
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

# setup_logging() するとログレベルが INFO になるので DEBUG に変更
logger.setLevel(logging.DEBUG)


YOUTUBE_DATA_API_KEY = os.environ['YOUTUBE_DATA_API_KEY']
JST = zoneinfo.ZoneInfo('Asia/Tokyo')

firestore_client = google.cloud.firestore.Client()

youtube = build(
    'youtube',
    'v3',
    developerKey=YOUTUBE_DATA_API_KEY
)


def post_message(webhook_url, headers, body):
    logger.info('----- post message -----')
    logger.debug(f'webhook_url={webhook_url}')
    logger.debug(f'headers={pformat(headers)}')
    logger.debug(f'body={pformat(body)}')

    response = requests.post(webhook_url, json.dumps(body), headers=headers)

    logger.debug(f'response.status={pformat(response.status_code)}')


def get_youtube_video_info(yt_videoid):
    logger.info('----- youtube api get video info -----')
    video_info = (
        youtube.videos()
        .list(
            id=yt_videoid,
            part="id, snippet, liveStreamingDetails, contentDetails",
            maxResults=1,
        )
        .execute()
        .get("items", [])[0]
    )
    logger.debug(f'video_info={pformat(video_info)}')
    return video_info


def get_youtube_channel_info(channel_id):
    logger.info('----- youtube api get channel info -----')
    channel_info = (
        youtube.channels()
        .list(
            id=channel_id,
            part="snippet",
            maxResults=1,
        )
        .execute()
        .get("items", [])[0]
    )
    logger.debug(f'channel_info={pformat(channel_info)}')
    return channel_info


def stream_notifier(event, context):
    logger.info('===== START youtube stream notifier =====')
    logger.info('event={}'.format(event))

    logger.info('----- get firestore webhook url -----')
    webhook_url = firestore_client.collection('discord_bot').document(
        'youtube').get().to_dict()['webhook']

    logger.info('----- get firestore channel info -----')
    channel_info_doc_refs = firestore_client.collection('discord_bot').document(
        'youtube').collection('channel_info').list_documents()

    logger.info('----- get rss -----')
    for doc_ref in channel_info_doc_refs:
        rss_url = doc_ref.get().to_dict()['rss']
        logger.debug(f'rss={rss_url}')

        feed = feedparser.parse(rss_url)

        if feed['status'] != 200:
            logger.info('failed rss request.')
            headers = {'Content-Type': 'application/json'}
            content = f'RSSの取得に失敗しました。{rss_url}'
            body = {
                'username': 'Youtube Stream Notifier',
                'content': content
            }
            post_message(webhook_url, headers, body)
            continue

        entry = feed['entries'][0]
        logger.debug(f'entry={pformat(entry)}')

        link = entry['link']
        title = entry['title']
        updated = entry['updated']
        yt_videoid = entry['yt_videoid']

        logger.info('----- get firestore video info -----')
        video_info_doc = doc_ref.collection('video_list').document(yt_videoid).get().to_dict()

        video_info = get_youtube_video_info(yt_videoid)

        logger.info('----- create or update firestore video info -----')
        doc_data = {
            'link': link,
            'title': title,
            'updated': updated
        }
        doc_ref.collection('video_list').document(yt_videoid).set(doc_data)

        is_streaming = 'actualStartTime' in video_info['liveStreamingDetails'] and 'actualEndTime' not in video_info['liveStreamingDetails']
        # firestoreにデータがないかつすでに開始されている
        if video_info_doc is None and is_streaming:
            channel_info = get_youtube_channel_info(video_info['snippet']['channelId'])

            headers = {'Content-Type': 'application/json'}
            content = link
            body = {
                'username': video_info['snippet']['channelTitle'],
                'avatar_url': channel_info['snippet']['thumbnails']['default']['url'],
                'content': content
            }
            post_message(webhook_url, headers, body)
        # firestoreにデータがあるかつ更新日時が異なるかつすでに開始されている
        elif video_info_doc is not None and video_info_doc['updated'] != updated and is_streaming:
            channel_info = get_youtube_channel_info(video_info['snippet']['channelId'])

            headers = {'Content-Type': 'application/json'}
            content = link
            body = {
                'username': video_info['snippet']['channelTitle'],
                'avatar_url': channel_info['snippet']['thumbnails']['default']['url'],
                'content': content
            }
            post_message(webhook_url, headers, body)


if __name__ == '__main__':
    stream_notifier('event', 'context')
