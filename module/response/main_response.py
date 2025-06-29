from llm_client import generate_emotion_from_prompt as estimate_emotion, generate_gpt_response, extract_emotion_summary
from response.response_index import search_similar_emotions
from response.response_long import match_long_keywords
from response.response_intermediate import match_intermediate_keywords
from response.response_short import match_short_keywords
from utils import logger
import time
import copy

def run_response_pipeline(user_input: str) -> tuple[str, dict]:
    initial_emotion = {}
    main_emotion = "未定義"
    used_llm_only = False  # ← LLMのみを使ったか記録するフラグ

    try:
        logger.info("[TIMER] ▼ ステップ① 感情推定 開始")
        print("🤔 ステップ①: 感情推定 開始")
        t1 = time.time()
        _, initial_emotion = estimate_emotion(user_input)
        print("🤔 感情推定結果:", initial_emotion)
        logger.info(f"[TIMER] ▲ ステップ① 感情推定 完了: {time.time() - t1:.2f}秒")

        if not isinstance(initial_emotion, dict):
            logger.error(f"[ERROR] 感情推定結果が辞書形式ではありません: {type(initial_emotion)} - {initial_emotion}")
            initial_emotion = {}

        main_emotion = initial_emotion.get("主感情", "未定義")
        logger.debug(f"[DEBUG] 推定された主感情: {main_emotion}")
        logger.info("[INFO] 感情推定完了")

    except Exception as e:
        logger.error(f"[ERROR] 感情推定中にエラー発生: {e}")
        raise

    try:
        logger.info("[TIMER] ▼ ステップ② 類似感情検索 開始")
        print("🔍 ステップ②: 類似感情検索 開始")
        t2 = time.time()
        top30_emotions = search_similar_emotions(initial_emotion)
        logger.info(f"[TIMER] ▲ ステップ② 類似感情検索 完了: {time.time() - t2:.2f}秒")

        logger.info(f"[検索結果] long: {len(top30_emotions.get('long', []))}件, intermediate: {len(top30_emotions.get('intermediate', []))}件, short: {len(top30_emotions.get('short', []))}件")

        logger.info("[TIMER] ▼ ステップ③ キーワードマッチ 開始")
        print("🤩 ステップ③: キーワードマッチング 開始")
        t3 = time.time()
        long_matches = match_long_keywords(initial_emotion, top30_emotions.get("long", []))
        intermediate_matches = match_intermediate_keywords(initial_emotion, top30_emotions.get("intermediate", []))
        short_matches = match_short_keywords(initial_emotion, top30_emotions.get("short", []))
        logger.info(f"[TIMER] ▲ ステップ③ キーワードマッチ 完了: {time.time() - t3:.2f}秒")

        reference_emotions = long_matches + intermediate_matches + short_matches

        if not reference_emotions:
            logger.info("[INFO] 類似感情が見つからなかったため、LLM応答を使用します")
            print("📬 類似感情なし → LLM 応答を使用します")
            response = generate_gpt_response(user_input, [])
            logger.debug(f"[DEBUG] GPT生成応答（類似なし）: {response}")
            logger.info("[INFO] 類似感情がなかったため、再推定せず初期感情を使用します")

            summary = extract_emotion_summary(initial_emotion)
            print("🦾 取得した感情データの内容:", initial_emotion)
            print("📊 構成比サマリ:", summary)
            logger.info(f"[INFO] 出力感情構成比: {summary}")
            used_llm_only = True  # ← LLMのみ使用と記録
            return response, initial_emotion

    except Exception as e:
        logger.error(f"[ERROR] 類似感情検索中にエラー発生: {e}")
        raise

    try:
        logger.info("[TIMER] ▼ ステップ④ GPT応答生成 開始")
        print("💬 ステップ④: GPT応答生成 開始")
        t4 = time.time()
        response = generate_gpt_response(user_input, reference_emotions)
        logger.debug(f"[DEBUG] GPT生成応答: {response}")
        print("📨 生成された返信:", response)
        logger.info(f"[TIMER] ▲ ステップ④ GPT応答生成 完了: {time.time() - t4:.2f}秒")

    except Exception as e:
        logger.error(f"[ERROR] GPT応答生成中にエラー発生: {e}")
        raise

    try:
        if not used_llm_only:  # ← LLMのみ使用した場合は感情再推定をスキップ
            logger.info("[TIMER] ▼ ステップ⑤ 応答に対する感情再推定 開始")
            print("🔁 ステップ⑤: 応答感情再推定 開始")
            t5 = time.time()

            if not isinstance(response, str):
                logger.error(f"[ERROR] 応答の型が文字列ではない: {type(response)} - {response}")

            safe_response = copy.deepcopy(response)
            _, response_emotion = estimate_emotion(safe_response)
            logger.debug(f"[DEBUG] 応答に対する感情推定結果: {response_emotion}")
            print("📂 保存対象の感情データ:", response_emotion)
            summary = extract_emotion_summary(response_emotion)
            print("📊 構成比サマリ:", summary)
            logger.info(f"[INFO] 出力感情構成比: {summary}")
            logger.info(f"[TIMER] ▲ ステップ⑤ 応答感情再推定 完了: {time.time() - t5:.2f}秒")
            return response, response_emotion

    except Exception as e:
        logger.error(f"[ERROR] 応答感情再推定中にエラー発生: {e}")

    return response, initial_emotion  # fallback
