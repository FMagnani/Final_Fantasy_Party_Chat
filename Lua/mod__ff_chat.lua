-- BizHawk Lua Script: Final Fantasy 1 - Chat mod RAM exporter
-- Exports the party data and the game state/location to JSON files (old + new snapshot each).
-- Compatible with NES Hawk / QuickNES cores in BizHawk

local CHARACTERS_OLD_FILE = "ff1_characters_old.json"
local CHARACTERS_NEW_FILE = "ff1_characters_new.json"
local GAME_STATE_OLD_FILE = "ff1_game_state_old.json"
local GAME_STATE_NEW_FILE = "ff1_game_state_new.json"

-- Run the extraction once every N frames (1 = every frame, 30 = about twice per second)
local UPDATE_EVERY_N_FRAMES = 30

-- ============================================================
-- Shared helpers
-- ============================================================

-- Reads a whole file, returns nil if it does not exist
local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    return content
end

local function write_file(path, content)
    local f = io.open(path, "w")
    if f then
        f:write(content)
        f:close()
    end
end

-- Simple custom JSON serializer (no external libraries required)
local function to_json(val)
    local t = type(val)
    if t == "number" or t == "boolean" then
        return tostring(val)
    elseif t == "string" then
        return string.format("%q", val)
    elseif t == "table" then
        local is_array = (#val > 0)
        local items = {}
        if is_array then
            for _, v in ipairs(val) do
                table.insert(items, to_json(v))
            end
            return "[" .. table.concat(items, ",") .. "]"
        else
            for k, v in pairs(val) do
                table.insert(items, string.format("%q:%s", tostring(k), to_json(v)))
            end
            return "{" .. table.concat(items, ",") .. "}"
        end
    end
    return "null"
end

-- 2. copy json_new into json_old, 3. dump the fresh state into json_new.
-- First run: seed both with the current state, so a stale file from a previous
-- session is never compared against.
local function rotate_and_dump(old_file, new_file, json_str, first_run)
    local previous = first_run and json_str or read_file(new_file)
    write_file(old_file, previous or json_str)
    write_file(new_file, json_str)
end

-- ============================================================
-- Game state
-- ============================================================

-- Map ID Lookups
local MAP_NAMES = {
    [0x00] = "Overworld",
    [0x01] = "Coneria (Town)",       [0x02] = "Coneria Castle 1F",    [0x03] = "Coneria Castle 2F",
    [0x04] = "Pravoka",              [0x05] = "Elfland (Town)",       [0x06] = "Elfland Castle",
    [0x07] = "Melmond",              [0x08] = "Crescent Lake",        [0x09] = "Gaia",
    [0x0A] = "Onrac",                [0x0B] = "Lefein",               [0x0C] = "Dragon Caves",
    [0x0D] = "Waterfall Cave",       [0x0E] = "Temple of Fiends",     [0x0F] = "Earth Cave 1F",
    [0x10] = "Earth Cave 2F",        [0x11] = "Earth Cave 3F",        [0x12] = "Earth Cave 4F",
    [0x13] = "Earth Cave 5F",        [0x14] = "Gurgu Volcano 1F",     [0x15] = "Gurgu Volcano 2F",
    [0x16] = "Gurgu Volcano 3F",     [0x17] = "Gurgu Volcano 4F",     [0x18] = "Gurgu Volcano 5F",
    [0x19] = "Ice Cave 1F",          [0x1A] = "Ice Cave 2F",          [0x1B] = "Ice Cave 3F",
    [0x1C] = "Castle of Ordeals 1F", [0x1D] = "Castle of Ordeals 2F", [0x1E] = "Castle of Ordeals 3F",
    [0x1F] = "Sea Shrine 1F",        [0x20] = "Sea Shrine 2F",        [0x21] = "Mirage Tower 1F"
}

-- Decode Game Phase
local function get_game_phase(screen_mode, map_context)
    if screen_mode == 0x02 or map_context == 0x02 then
        return "Battle"
    elseif screen_mode == 0x03 then
        return "Menu / Shop"
    elseif screen_mode == 0x01 or map_context == 0x00 or map_context == 0x01 then
        return "Exploration"
    end
    return "Other / Transition"
end

-- Decode Location Type
local function get_location_type(map_context, map_id)
    if map_context == 0x00 then
        return "Overworld"
    elseif map_context == 0x01 then
        -- Map IDs 0x01, 0x04, 0x05, 0x07, 0x08, 0x09, 0x0A, 0x0B are towns
        if (map_id >= 0x01 and map_id <= 0x0B and map_id ~= 0x02 and map_id ~= 0x03 and map_id ~= 0x06) then
            return "Town"
        elseif map_id == 0x02 or map_id == 0x03 or map_id == 0x06 then
            return "Castle"
        else
            return "Dungeon"
        end
    elseif map_context == 0x02 then
        return "Battle Arena"
    end
    return "Unknown"
end

-- Decode Battle Encounter Type
local function get_battle_type(ambush_byte)
    if ambush_byte == 0x00 then return "Normal"
    elseif ambush_byte == 0x01 then return "Pre-emptive Strike"
    elseif ambush_byte == 0x02 then return "Ambushed"
    end
    return "None"
end

local function extract_game_state()
    -- Read RAM Values
    local screen_mode  = memory.readbyte(0x0000, "System Bus")
    local map_context  = memory.readbyte(0x000D, "System Bus")
    local map_id       = memory.readbyte(0x0020, "System Bus")
    local pos_x        = memory.readbyte(0x0068, "System Bus")
    local pos_y        = memory.readbyte(0x0069, "System Bus")
    local steps_left   = memory.readbyte(0x00F5, "System Bus")
    local next_enemy   = memory.readbyte(0x00F7, "System Bus")
    local ambush_state = memory.readbyte(0x6800, "System Bus")

    local phase        = get_game_phase(screen_mode, map_context)
    local loc_type     = get_location_type(map_context, map_id)
    local map_name     = MAP_NAMES[map_id] or string.format("Unknown Map (0x%02X)", map_id)

    -- Vehicle/Transport detection
    local vehicle_byte = memory.readbyte(0x002D, "System Bus")
    local vehicle_str  = "Walking"
    if vehicle_byte == 0x01 then vehicle_str = "Canoe"
    elseif vehicle_byte == 0x02 then vehicle_str = "Ship"
    elseif vehicle_byte == 0x04 then vehicle_str = "Airship"
    end

    return {
        frame = emu.framecount(),
        game_phase = phase,             -- "Exploration", "Battle", "Menu / Shop"
        in_combat = (phase == "Battle"),
        location = {
            map_id = map_id,
            map_name = map_name,         -- e.g. "Coneria (Town)"
            type = loc_type,             -- "Overworld", "Town", "Castle", "Dungeon"
            coordinates = {
                x = pos_x,
                y = pos_y
            },
            transportation = vehicle_str -- "Walking", "Canoe", "Ship", "Airship"
        },
        encounter_data = {
            steps_until_encounter = steps_left,
            next_enemy_group_id = next_enemy,
            battle_condition = (phase == "Battle") and get_battle_type(ambush_state) or "None"
        }
    }
end

-- ============================================================
-- Characters
-- ============================================================

-- Character base RAM offsets (Character 1 through 4)
local CHAR_OFFSETS = { 0x6100, 0x6140, 0x6180, 0x61C0 }
local MAGIC_OFFSETS = { 0x6300, 0x6330, 0x6360, 0x6390 }

-- Status flags mapping
local function parse_status(status_byte)
    if status_byte == 0 then return { "Normal" } end
    local statuses = {}
    if math.floor(status_byte / 1) % 2 == 1 then table.insert(statuses, "Dead") end
    if math.floor(status_byte / 2) % 2 == 1 then table.insert(statuses, "Petrified") end
    if math.floor(status_byte / 4) % 2 == 1 then table.insert(statuses, "Low HP") end
    if math.floor(status_byte / 8) % 2 == 1 then table.insert(statuses, "Poison") end
    if math.floor(status_byte / 16) % 2 == 1 then table.insert(statuses, "Darkness") end
    if math.floor(status_byte / 32) % 2 == 1 then table.insert(statuses, "Stun") end
    if math.floor(status_byte / 64) % 2 == 1 then table.insert(statuses, "Sleep") end
    if math.floor(status_byte / 128) % 2 == 1 then table.insert(statuses, "Mute") end
    return statuses
end

-- Helper to read 16-bit Little-Endian integer
local function read16(addr)
    local low = memory.readbyte(addr, "System Bus")
    local high = memory.readbyte(addr + 1, "System Bus")
    return low + (high * 256)
end

-- Map Class IDs to Names
local CLASS_MAP = {
    [0x00] = "Fighter",      [0x06] = "Knight",
    [0x01] = "Thief",        [0x07] = "Ninja",
    [0x02] = "Black Belt",   [0x08] = "Master",
    [0x03] = "Red Mage",     [0x09] = "Red Wizard",
    [0x04] = "White Mage",   [0x0A] = "White Wizard",
    [0x05] = "Black Mage",   [0x0B] = "Black Wizard"
}

-- Official NES Final Fantasy 1 Name Decoding Table
local FF1_NAME_TABLE = {
    -- Numbers (0x80 - 0x89)
    [0x80]="0", [0x81]="1", [0x82]="2", [0x83]="3", [0x84]="4",
    [0x85]="5", [0x86]="6", [0x87]="7", [0x88]="8", [0x89]="9",

    -- Uppercase Letters A-Z (0x8A - 0xA3)
    [0x8A]="A", [0x8B]="B", [0x8C]="C", [0x8D]="D", [0x8E]="E", [0x8F]="F",
    [0x90]="G", [0x91]="H", [0x92]="I", [0x93]="J", [0x94]="K", [0x95]="L",
    [0x96]="M", [0x97]="N", [0x98]="O", [0x99]="P", [0x9A]="Q", [0x9B]="R",
    [0x9C]="S", [0x9D]="T", [0x9E]="U", [0x9F]="V", [0xA0]="W", [0xA1]="X",
    [0xA2]="Y", [0xA3]="Z",

    -- Lowercase Letters a-z (0xA4 - 0xBD)
    [0xA4]="a", [0xA5]="b", [0xA6]="c", [0xA7]="d", [0xA8]="e", [0xA9]="f",
    [0xAA]="g", [0xAB]="h", [0xAC]="i", [0xAD]="j", [0xAE]="k", [0xAF]="l",
    [0xB0]="m", [0xB1]="n", [0xB2]="o", [0xB3]="p", [0xB4]="q", [0xB5]="r",
    [0xB6]="s", [0xB7]="t", [0xB8]="u", [0xB9]="v", [0xBA]="w", [0xBB]="x",
    [0xBC]="y", [0xBD]="z"
}

-- Decode character name from RAM (6 bytes from offset 0x02 to 0x07)
local function read_character_name(base_addr)
    local name = ""
    for i = 0, 5 do
        local b = memory.readbyte(base_addr + 0x02 + i, "System Bus")
        if FF1_NAME_TABLE[b] then
            name = name .. FF1_NAME_TABLE[b]
        end
    end
    return name ~= "" and name or "UNNAMED"
end

local function get_character_data(slot)
    local base = CHAR_OFFSETS[slot]
    local m_base = MAGIC_OFFSETS[slot]

    local name_str   = read_character_name(base)

    -- Raw status & ID
    local char_id     = memory.readbyte(base + 0x00, "System Bus")
    local status_byte = memory.readbyte(base + 0x01, "System Bus")
    local class_name  = CLASS_MAP[char_id] or "Unknown"

    -- Stats
    local current_xp  = read16(base + 0x07)
    local current_hp  = read16(base + 0x0A)
    local max_hp      = read16(base + 0x0C)
    local str         = memory.readbyte(base + 0x10, "System Bus")
    local agi         = memory.readbyte(base + 0x11, "System Bus")
    local int_stat    = memory.readbyte(base + 0x12, "System Bus")
    local vit         = memory.readbyte(base + 0x13, "System Bus")
    local luk         = memory.readbyte(base + 0x14, "System Bus")
    local required_xp = read16(base + 0x16)
    local level       = memory.readbyte(base + 0x26, "System Bus") + 1

    -- Combat Derived Stats
    local max_dmg     = memory.readbyte(base + 0x20, "System Bus")
    local hit_pct     = memory.readbyte(base + 0x21, "System Bus")
    local absorb      = memory.readbyte(base + 0x22, "System Bus")
    local evade       = memory.readbyte(base + 0x23, "System Bus")

    -- Equipment IDs
    local weapons = {
        memory.readbyte(base + 0x18, "System Bus"),
        memory.readbyte(base + 0x19, "System Bus"),
        memory.readbyte(base + 0x1A, "System Bus"),
        memory.readbyte(base + 0x1B, "System Bus")
    }
    local armor = {
        memory.readbyte(base + 0x1C, "System Bus"),
        memory.readbyte(base + 0x1D, "System Bus"),
        memory.readbyte(base + 0x1E, "System Bus"),
        memory.readbyte(base + 0x1F, "System Bus")
    }

    -- Magic MP Max & Current (Levels 1-8)
    local max_mp = {}
    local current_mp = {}
    for lvl = 0, 7 do
        table.insert(max_mp, memory.readbyte(m_base + 0x20 + lvl, "System Bus"))
        table.insert(current_mp, memory.readbyte(m_base + 0x28 + lvl, "System Bus"))
    end

    -- Magic Spells Known per tier (3 spells per level)
    local spells = {}
    local spell_offsets = { 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C }
    for lvl = 1, 8 do
        local tier_offset = spell_offsets[lvl]
        spells["level_" .. lvl] = {
            memory.readbyte(m_base + tier_offset + 0, "System Bus"),
            memory.readbyte(m_base + tier_offset + 1, "System Bus"),
            memory.readbyte(m_base + tier_offset + 2, "System Bus")
        }
    end

    return {
        slot = slot,
        name = name_str,
        class = class_name,
        class_id = char_id,
        character_id = char_id,
        level = level,
        status = parse_status(status_byte),
        hp = { current = current_hp, max = max_hp },
        experience = { current = current_xp, next_level = required_xp },
        attributes = {
            strength = str,
            agility = agi,
            intelligence = int_stat,
            vitality = vit,
            luck = luk
        },
        derived_stats = {
            damage = max_dmg,
            hit_percent = hit_pct,
            absorb = absorb,
            evade = evade
        },
        equipment = {
            weapons = weapons,
            armor = armor
        },
        magic = {
            current_mp = current_mp,
            max_mp = max_mp,
            spells = spells
        }
    }
end

local function extract_characters()
    local party = {}
    for slot = 1, 4 do
        table.insert(party, get_character_data(slot))
    end
    return {
        frame = emu.framecount(),
        party = party
    }
end

-- ============================================================
-- Main Loop
-- ============================================================
local first_run = true
while true do
    -- No ROM loaded (NullHawk core has no memory domains): wait instead of erroring.
    -- first_run stays true, so the files are seeded as soon as a game starts.
    if emu.getsystemid() == "NULL" then
        emu.frameadvance()
        goto continue
    end

    -- 1. extract RAM
    local characters_json = to_json(extract_characters())
    local game_state_json = to_json(extract_game_state())

    -- 2 + 3. old <- new, new <- fresh dump
    rotate_and_dump(CHARACTERS_OLD_FILE, CHARACTERS_NEW_FILE, characters_json, first_run)
    rotate_and_dump(GAME_STATE_OLD_FILE, GAME_STATE_NEW_FILE, game_state_json, first_run)
    first_run = false

    for _ = 1, UPDATE_EVERY_N_FRAMES do
        emu.frameadvance()
    end

    ::continue::
end
