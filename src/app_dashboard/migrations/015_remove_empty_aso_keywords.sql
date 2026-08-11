-- A dimension advertised by GA4 metadata is not necessarily populated.
-- Blank searchTerm values are channel traffic, not observed keywords.
update aso_source_capabilities capability
set status = 'unsupported',
    error_code = 'NoKeywordValues',
    checked_at = now()
where capability.source = 'aso_keywords'
  and capability.status in ('ready', 'partial')
  and exists (
      select 1 from aso_keyword_daily keyword
      where keyword.app_id = capability.app_id
  )
  and not exists (
      select 1 from aso_keyword_daily keyword
      where keyword.app_id = capability.app_id
        and btrim(keyword.keyword) <> ''
  );

delete from aso_keyword_daily where btrim(keyword) = '';
