#!/bin/bash
SUB_LEVELS="50"
MATRIX_JSON='[{"sub_level":"50","os_patch_level":"2024-10"},{"sub_level":"X","os_patch_level":"lts"}]'

JSON_OUT=$(echo "$MATRIX_JSON" | jq --arg subs ",$SUB_LEVELS," -c 'map(select($subs | contains("," + .sub_level + ",")))')
echo "$JSON_OUT"
