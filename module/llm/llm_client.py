#module/llm/llm_client.py
from openai import OpenAI
import re
import json
import os
from datetime import datetime

from utils import (
    load_system_prompt_cached,
    load_emotion_prompt,
    load_dialogue_prompt,
    logger
)

from module.memory.oblivion_emotion import clean_old_emotions
from module.context.context_selector import select_contextual_history  # ← 未使用（将来用）
from module.memory.index_emotion import extract_personality_tendency
from module.mongo.emotion_dataset import get_recent_dialogue_history
from module.params import (
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    OPENAI_TOP_P,
    OPENAI_MAX_TOKENS
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate_gpt_response_from_history() -> str:
    """
    MongoDBから直近3件の対話履歴を取得し、それをもとにGPT応答を生成。
    応答内容は文字列で返却（JSON抽出は別モジュールで処理）。
    """
    logger.info("[START] generate_gpt_response_from_history")

    system_prompt = load_system_prompt_cached()
    user_prompt = load_dialogue_prompt()

    # 履歴取得（直近3件）
    logger.info("[INFO] 履歴取得中...")
    selected_history = get_recent_dialogue_history(3)
    logger.info(f"[INFO] 履歴件数: {len(selected_history)} 件")

    try:
        logger.info("[INFO] OpenAI呼び出し開始")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                *[{"role": entry["role"], "content": entry["message"]} for entry in selected_history],
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=OPENAI_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE,
            top_p=OPENAI_TOP_P
        )
        logger.info("[INFO] OpenAI応答取得完了")
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR] OpenAI呼び出し失敗: {e}")
        return "申し訳ありません、ご主人。応答生成中にエラーが発生しました。"


def generate_emotion_from_prompt_with_context(user_input: str, emotion_structure: dict) -> tuple[str, dict]:
    system_prompt = load_system_prompt_cached()
    user_prompt = load_dialogue_prompt()

    # 🔸 人格傾向の取得と整形
    personality = extract_personality_tendency()  # ← ここが人格データの取得
    personality_text = "\n【人格傾向】\nこのAIは以下の感情を持つ傾向があります：\n"
    if personality:
        for emotion, count in personality.items():
            personality_text += f"・{emotion}（{count}回）\n"
    else:
        personality_text += "傾向情報がまだ十分にありません。\n"

    # 🔸 感情インデックス検索
    match = search_index_response(emotion_structure.get("構成比", {}), emotion_structure.get("keywords", []))

    if match is None:
        # 🔻 条件1：インデックス検索で見つからなかった場合 → 履歴のみで生成
        fallback_response = generate_gpt_response_from_history()

        # 🔸 人格傾向 + 履歴のみで構成
        prompt = (
            f"{user_prompt}\n\n"
            f"{personality_text}\n"  # ← ここも人格テキストを含める箇所
            f"ユーザー発言: {user_input}\n"
            f"履歴応答: {fallback_response}\n\n"
            f"【指示】上記の人格傾向と履歴を参考に、自然な応答のみを生成してください。\n"
            f"構成比や感情構造は不要です。"
        )
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=OPENAI_MAX_TOKENS,
                temperature=OPENAI_TEMPERATURE,
                top_p=OPENAI_TOP_P
            )
            return response.choices[0].message.content.strip(), {}
        except Exception as e:
            logger.error(f"[ERROR] 応答生成失敗: {e}")
            return "応答生成でエラーが発生しました。", {}

    else:
        # 🔻 条件2：インデックスに一致 → さらに履歴取得
        emotion_name = match.get("emotion")
        date_str = match.get("date")
        history_data = collect_all_category_responses(emotion_name, date_str)

        reference_emotions = []
        for category in ["short", "intermediate", "long"]:
            data = history_data.get(category)
            if data:
                reference_emotions.append(data)

        # 参考感情データ整形
        reference_text = "\n\n【参考感情データ】\n"
        for i, item in enumerate(reference_emotions, 1):
            reference_text += f"\n● ケース{i}\n"
            reference_text += f"主感情: {item.get('主感情')}\n"
            reference_text += f"構成比: {item.get('構成比')}\n"
            reference_text += f"状況: {item.get('状況')}\n"
            reference_text += f"心理反応: {item.get('心理反応')}\n"
            reference_text += f"キーワード: {', '.join(item.get('keywords', []))}\n"

        # 🔸 人格傾向 + 感情記録でプロンプト構築
        prompt = (
            f"{user_prompt}\n\n"
            f"{personality_text}\n"  # ← 人格テキスト挿入位置
            f"ユーザー発言: {user_input}\n"
            f"{reference_text}\n\n"
            f"【指示】上記の感情参照データと人格傾向を参考に、emotion_promptのルールに従って応答を生成してください。"
            f"自然な応答 + 構成比 + JSON形式の感情構造の順で出力してください。"
        )

        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=OPENAI_MAX_TOKENS,
                temperature=OPENAI_TEMPERATURE,
                top_p=OPENAI_TOP_P
            )

            full_response = response.choices[0].message.content.strip()
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", full_response, re.DOTALL)
            if json_match:
                try:
                    emotion_data = json.loads(json_match.group(1))
                    emotion_data["date"] = datetime.now().strftime("%Y%m%d%H%M%S")
                    return re.sub(r"```json\s*\{.*?\}\s*```", "", full_response, flags=re.DOTALL).strip(), emotion_data
                except Exception as e:
                    logger.error(f"[ERROR] JSONパース失敗: {e}")
                    return full_response, {}
            else:
                return full_response, {}

        except Exception as e:
            logger.error(f"[ERROR] 応答生成失敗: {e}")
            return "応答生成でエラーが発生しました。", {}
