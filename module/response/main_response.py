from llm_client import generate_emotion_from_prompt_simple as estimate_emotion, generate_emotion_from_prompt_with_context, extract_emotion_summary
from response.response_index import load_and_categorize_index, extract_best_reference, find_best_match_by_composition
from utils import logger, get_mongo_client, load_current_emotion, save_current_emotion, merge_emotion_vectors
from module.memory.main_memory import handle_emotion, save_emotion_sample, append_emotion_history, pad_emotion_vector
from module.memory.emotion_stats import synthesize_current_emotion
import json
import os
from bson import ObjectId
from utils import summarize_feeling


from module.response.response_long import find_history_by_emotion_and_date as find_long
from module.response.response_short import find_history_by_emotion_and_date as find_short
from module.response.response_intermediate import find_history_by_emotion_and_date as find_intermediate
from module.mongo.mongo_client import get_mongo_client  # 新たな統一モジュールを使用

client = get_mongo_client()
if client is None:
    raise ConnectionError("[ERROR] MongoDBクライアントの取得に失敗しました")
db = client["emotion_db"]

def get_mongo_collection(category, emotion_label):
    try:
        client = get_mongo_client()
        if client is None:
            raise ConnectionError("MongoDBクライアントの取得に失敗しました")

        db = client["emotion_db"]
        collection_name = f"{category}_{emotion_label}"
        return db[collection_name]
    except Exception as e:
        logger.error(f"[ERROR] MongoDBコレクション取得失敗: {e}")
        return None

def find_response_by_emotion(emotion_structure: dict) -> dict:　#LLMの初期応答で取得したキーワードと感情構成比、各responceで処理
    composition = emotion_structure.get("構成比", {})
    keywords = emotion_structure.get("keywords", [])

def collect_all_category_responses(emotion_name: str, date_str: str) -> dict:
    """
    各カテゴリ（short → intermediate → long）から指定された感情名と日付に一致する履歴を取得する。
    """
    short_data = find_short(emotion_name, "short", date_str)
    intermediate_data = find_intermediate(emotion_name, "intermediate", date_str)
    long_data = find_long(emotion_name, "long", date_str)

    return {
        "short": short_data,
        "intermediate": intermediate_data,
        "long": long_data
    }


#↑7/20ここまで



def load_emotion_by_date(path, target_date):
    if path.startswith("mongo/"):
        try:
            parts = path.split("/")
            if len(parts) == 3:
                _, category, emotion_label = parts

                try:
                    db.client.admin.command("ping")
                except Exception as e:
                    return None

                collection = db["emotion_data"]
                doc = collection.find_one({"category": category, "emotion": emotion_label})

                if doc and "data" in doc and "履歴" in doc["data"]:
                    for entry in doc["data"]["履歴"]:
                        if str(entry.get("date")) == str(target_date):
                            return entry
            return None
        except Exception as e:
            logger.error(f"[ERROR] MongoDBデータ取得失敗: {e}")
            return None

    try:
        if not os.path.exists(path):
            logger.warning(f"[WARNING] 指定されたパスが存在しません: {path}")
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            for item in reversed(data):
                if str(item.get("date")) == str(target_date):
                    return item

        elif isinstance(data, dict) and "履歴" in data:
            for item in reversed(data["履歴"]):
                if str(item.get("date")) == str(target_date):
                    return item

    except Exception as e:
        logger.error(f"[ERROR] 感情データの読み込み失敗: {e}")
    return None

def run_response_pipeline(user_input: str) -> tuple[str, dict]:
    initial_emotion = {}
    reference_emotions = []
    best_match = None

    current_feeling = load_current_emotion()

    try:
        raw_response, initial_emotion = estimate_emotion(user_input, current_emotion=current_feeling)

        # ✅ 追加ログ出力
        print("📝 [LLM初期応答文] " + raw_response)
        print(f"🔍 [初期感情構成比] {initial_emotion.get('構成比', {})}")
        print(f"🔑 [検索用キーワード] {initial_emotion.get('keywords', [])}")

        save_emotion_sample(user_input, raw_response, initial_emotion.get("構成比", {}))
    except Exception as e:
        logger.error(f"[ERROR] 感情推定中にエラー発生: {e}")
        raise

    try:
        categorized = load_and_categorize_index()
    except Exception as e:
        logger.error(f"[ERROR] インデックス読み込み中にエラー発生: {e}")
        raise

    try:
        for category in ["short", "intermediate", "long"]:
            refer = extract_best_reference(initial_emotion, categorized.get(category, []), category)
            if refer:
                emotion_data = refer.get("emotion", {})
                path = refer.get("保存先")
                date = refer.get("date")
                full_emotion = load_emotion_by_date(path, date) if path and date else None
                if full_emotion:
                    reference_emotions.append({
                        "emotion": full_emotion,
                        "source": refer.get("source"),
                        "match_info": refer.get("match_info", "")
                    })
        best_match = find_best_match_by_composition(initial_emotion["構成比"], [r["emotion"] for r in reference_emotions])

        if best_match is None:
            final_response = raw_response
            response_emotion = initial_emotion
        else:
            context = [best_match]
            context.append({
                "emotion": {
                    "現在の気分": current_feeling
                },
                "source": "現在の気分合成データ",
                "match_info": "現在の気分のプロンプト挿入用"
            })
            final_response, response_emotion = generate_emotion_from_prompt_with_context(user_input, context)

    except Exception as e:
        logger.error(f"[ERROR] GPT応答生成中にエラー発生: {e}")
        raise

        if best_match:
            print("📌 参照感情データ:")
            for idx, emo_entry in enumerate(reference_emotions, start=1):
                emo = emo_entry["emotion"]
                ratio = emo.get("構成比", {})
                summary_str = ", ".join([f"{k}:{v}%" for k, v in ratio.items()])
                print(f"  [{idx}] {summary_str} | 状況: {emo.get('状況', '')} | キーワード: {', '.join(emo.get('keywords', []))}（{emo_entry.get('match_info', '')}｜{emo_entry.get('source', '不明')}）")
        else:
            print("📌 参照感情データ: 参照なし")

    try:
        reference_data = best_match if isinstance(best_match, dict) else {"構成比": {}, "source": "不明", "date": "不明"}

        response_emotion["emotion_vector"] = response_emotion.get("構成比", {})
        handle_emotion(response_emotion, user_input=user_input, response_text=final_response)

        padded_ratio = pad_emotion_vector(response_emotion.get("構成比", {}))
        response_emotion["構成比"] = padded_ratio
        append_emotion_history(response_emotion)

        merged = merge_emotion_vectors(current_feeling, response_emotion.get("構成比", {}))
        save_current_emotion(merged)

        return final_response, response_emotion
    except Exception as e:
        logger.error(f"[ERROR] 最終応答ログ出力中にエラー発生: {e}")
        raise

