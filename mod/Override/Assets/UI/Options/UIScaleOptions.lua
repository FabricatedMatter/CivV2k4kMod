----------------------------------------------------------------
-- Civ V UI Scale mod: the UI Scale controls in the options menu.
-- version.dll patches whichever OptionsMenu the game loads (base game, EUI, ...) to
-- include this file at its end and to contain the (hidden) UIScaleBlock controls; see
-- native/src/optionspatch.cpp. The native side exposes the global table UIScale.
-- Set by the patch: UIScaleParentID (the control holding UIScaleBlock).
-- Called by the patch: UIScaleOnShowHide(isHide, isInit) after the menu's own handler.
----------------------------------------------------------------
print("[UIScale] OptionsMenu: UIScale=" .. tostring(UIScale) .. " block=" .. tostring(Controls.UIScaleBlock)
	.. " parent=" .. tostring(UIScaleParentID));

local UIS_MIN, UIS_MAX, UIS_STEP = 0.5, 3.0, 0.05;
local g_uisPending = 0;   -- 0 = automatic

local function UIScaleAvailable()
	return UIScale ~= nil and UIScale.IsActive() and Controls.UIScaleBlock ~= nil;
end

local function UIScaleRound(v)
	return math.floor(v / UIS_STEP + 0.5) * UIS_STEP;
end

-- Stacks only lay out on demand; the parent may be any control (pcall: not all of them
-- have these methods).
local function UIScaleLayout(c)
	if c ~= nil then
		pcall(function() c:CalculateSize(); end);
		pcall(function() c:ReprocessAnchoring(); end);
	end
end

local function UIScaleSetValueText(v)
	Controls.UIScaleValue:SetText(string.format("UI Scale: %d%%", math.floor(v * 100 + 0.5)));
	UIScaleLayout(Controls.UIScaleRow);
end

local function UIScaleRefresh()
	local bAuto = (g_uisPending <= 0);
	local shown = bAuto and UIScale.GetAuto() or g_uisPending;
	UIScaleSetValueText(shown);
	Controls.UIScaleAutoCheck:SetCheck(bAuto);
	Controls.UIScaleSlider:SetValue((math.max(UIS_MIN, math.min(UIS_MAX, shown)) - UIS_MIN) / (UIS_MAX - UIS_MIN));
	Controls.UIScaleSlider:SetDisabled(bAuto);
	Controls.UIScaleSlider:SetAlpha(bAuto and 0.5 or 1.0);
end

local function UIScaleCommit()
	if UIScaleAvailable() and math.abs(UIScale.GetSetting() - g_uisPending) > 0.001 then
		UIScale.Set(g_uisPending);
	end
end

if UIScaleAvailable() then
	-- Safe mode: the DLL could not hook the graphics this session; say so where the scale lives.
	local warning = UIScale.GetWarning ~= nil and UIScale.GetWarning() or nil;
	if warning then
		Controls.UIScaleWarning:SetText("[COLOR_WARNING_TEXT]" .. warning .. "[ENDCOLOR]");
		Controls.UIScaleWarning:SetHide(false);
	end

	Controls.UIScaleBlock:SetHide(false);
	UIScaleLayout(Controls.UIScaleRow);
	UIScaleLayout(Controls.UIScaleBlock);
	UIScaleLayout(UIScaleParentID ~= nil and Controls[UIScaleParentID] or nil);

	Controls.UIScaleSlider:RegisterSliderCallback(function(fValue)
		if g_uisPending <= 0 then return; end
		g_uisPending = UIScaleRound(UIS_MIN + fValue * (UIS_MAX - UIS_MIN));
		UIScaleSetValueText(g_uisPending);
	end);

	Controls.UIScaleAutoCheck:RegisterCheckHandler(function(bChecked)
		if bChecked then
			g_uisPending = 0;
		else
			g_uisPending = UIScaleRound(UIScale.Get());
		end
		UIScaleRefresh();
	end);

	Controls.UIScaleApplyButton:RegisterCallback(Mouse.eLClick, UIScaleCommit);

	-- Accept: the patch's prologue calls this from OptionsManager.CommitGraphicsOptions, which
	-- only the Accept handler calls (base game and EUI alike).
	UIScaleOnAccept = UIScaleCommit;

	-- Reopening the menu (e.g. after Cancel) shows the saved setting again.
	UIScaleOnShowHide = function(bIsHide, bIsInit)
		if not bIsHide then
			g_uisPending = UIScale.GetSetting();
			UIScaleRefresh();
		end
	end

	g_uisPending = UIScale.GetSetting();
	UIScaleRefresh();
end
