"""Data Dragon 챔피언 매핑 생성을 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path

from src.champions import ChampionRepository


class ChampionRepositoryTests(unittest.TestCase):
    """일반 챔피언과 변형 챔피언의 이름 충돌을 검증한다."""

    def test_jade_variant_does_not_replace_standard_champion(self) -> None:
        payload = {
            "data": {
                "Ahri": {
                    "id": "Ahri",
                    "key": "103",
                    "name": "아리",
                    "spells": [
                        {
                            "id": "AhriQ",
                            "name": "현혹의 구슬",
                            "image": {"full": "AhriQ.png"},
                        }
                    ],
                },
                "Jade_Ahri": {
                    "id": "Jade_Ahri",
                    "key": "60103",
                    "name": "아리",
                    "spells": [
                        {
                            "id": "Jade_AhriQ",
                            "name": "현혹의 구슬",
                            "image": {"full": "Jade_AhriQ.png"},
                        }
                    ],
                },
            }
        }

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp_dir:
            repository = ChampionRepository(str(Path(temp_dir) / "champions.db"))
            champion_data = repository._build_data("16.15.1", json.dumps(payload))

        self.assertEqual(champion_data.korean_names, ["아리"])
        self.assertEqual(champion_data.to_english_key("아리"), "Ahri")
        self.assertEqual(champion_data.to_korean_name("Ahri"), "아리")
        self.assertEqual(champion_data.to_korean_name("Jade_Ahri"), "Jade_Ahri")
        self.assertEqual(champion_data.spell_info("Ahri", 1)["id"], "AhriQ")
        self.assertIsNone(champion_data.spell_info("Jade_Ahri", 1))


if __name__ == "__main__":
    unittest.main()
