package compose

import (
	"fmt"

	"github.com/dlclark/regexp2"
)

// Region is one row of the region table (docs/routing.md §5.3). The UI only receives the
// code and names regions itself; the names here name the generated region groups.
type Region struct {
	Code   string // ISO 3166-1 alpha-2; OtherRegion for nodes no row matches
	Flag   string
	NameZH string
	NameEN string
	// Pattern finds the region in a node name, with mihomo's regular expressions
	// (regexp2). Codes match in upper case only and not inside a word, so "US" finds
	// "US 01" and "US01" but not "RUSSIA" or "plus".
	Pattern string

	re *regexp2.Regexp
}

// OtherRegion collects the nodes that no row of the table matches (CLDR's "unknown region").
const OtherRegion = "ZZ"

// code matches region codes as words of their own: not after or before another letter,
// so digits may follow ("HK01").
func code(alternatives string) string {
	return `(?<![A-Za-z])(?:` + alternatives + `)(?![A-Za-z])`
}

// Regions is the table in the order it is searched: the first row that matches a node name
// wins, so a node belongs to one region only. The order settles names that mention two
// places, such as "香港 IPLC 日本" or "🇨🇳 台湾".
var Regions = []*Region{
	{Code: "HK", Flag: "🇭🇰", NameZH: "香港", NameEN: "Hong Kong",
		Pattern: `🇭🇰|香港|港|(?i:hong ?kong)|` + code("HK|HKG")},
	{Code: "TW", Flag: "🇹🇼", NameZH: "台湾", NameEN: "Taiwan",
		Pattern: `🇹🇼|台湾|臺灣|台灣|台北|新北|彰化|(?i:taiwan|taipei)|` + code("TW|TWN")},
	{Code: "MO", Flag: "🇲🇴", NameZH: "澳门", NameEN: "Macao",
		Pattern: `🇲🇴|澳门|澳門|(?i:maca[ou])|` + code("MO|MAC")},
	{Code: "JP", Flag: "🇯🇵", NameZH: "日本", NameEN: "Japan",
		Pattern: `🇯🇵|日本|东京|東京|大阪|埼玉|(?i:japan|tokyo|osaka)|` + code("JP|JPN")},
	{Code: "KR", Flag: "🇰🇷", NameZH: "韩国", NameEN: "South Korea",
		Pattern: `🇰🇷|韩国|韓國|首尔|首爾|春川|(?i:korea|seoul)|` + code("KR|KOR")},
	{Code: "SG", Flag: "🇸🇬", NameZH: "新加坡", NameEN: "Singapore",
		Pattern: `🇸🇬|新加坡|狮城|獅城|(?i:singapore)|` + code("SG|SGP")},
	{Code: "US", Flag: "🇺🇸", NameZH: "美国", NameEN: "United States",
		Pattern: `🇺🇸|美国|美國|洛杉矶|洛杉磯|圣何塞|聖何塞|硅谷|西雅图|西雅圖|纽约|紐約|芝加哥|达拉斯|達拉斯|` +
			`(?i:united ?states|los ?angeles|san ?jose|silicon ?valley|seattle|new ?york|chicago|dallas)|` +
			code("US|USA")},
	{Code: "CA", Flag: "🇨🇦", NameZH: "加拿大", NameEN: "Canada",
		Pattern: `🇨🇦|加拿大|多伦多|多倫多|温哥华|溫哥華|蒙特利尔|(?i:canada|toronto|vancouver|montreal)|` + code("CA|CAN")},
	{Code: "GB", Flag: "🇬🇧", NameZH: "英国", NameEN: "United Kingdom",
		Pattern: `🇬🇧|英国|英國|伦敦|倫敦|(?i:united ?kingdom|britain|london)|` + code("UK|GB|GBR")},
	{Code: "FR", Flag: "🇫🇷", NameZH: "法国", NameEN: "France",
		Pattern: `🇫🇷|法国|法國|巴黎|(?i:france|paris)|` + code("FR|FRA")},
	{Code: "DE", Flag: "🇩🇪", NameZH: "德国", NameEN: "Germany",
		Pattern: `🇩🇪|德国|德國|法兰克福|法蘭克福|(?i:germany|frankfurt)|` + code("DE|DEU")},
	{Code: "NL", Flag: "🇳🇱", NameZH: "荷兰", NameEN: "Netherlands",
		Pattern: `🇳🇱|荷兰|荷蘭|阿姆斯特丹|(?i:netherlands|holland|amsterdam)|` + code("NL|NLD")},
	{Code: "RU", Flag: "🇷🇺", NameZH: "俄罗斯", NameEN: "Russia",
		Pattern: `🇷🇺|俄罗斯|俄羅斯|莫斯科|(?i:russia|moscow)|` + code("RU|RUS")},
	{Code: "TR", Flag: "🇹🇷", NameZH: "土耳其", NameEN: "Türkiye",
		Pattern: `🇹🇷|土耳其|伊斯坦布尔|(?i:turkey|türkiye|istanbul)|` + code("TR|TUR")},
	{Code: "ID", Flag: "🇮🇩", NameZH: "印度尼西亚", NameEN: "Indonesia",
		Pattern: `🇮🇩|印尼|印度尼西亚|印度尼西亞|雅加达|雅加達|(?i:indonesia|jakarta)|` + code("ID|IDN")},
	{Code: "IN", Flag: "🇮🇳", NameZH: "印度", NameEN: "India",
		Pattern: `🇮🇳|印度(?!尼)|孟买|孟買|(?i:india|mumbai)|` + code("IN|IND")},
	{Code: "TH", Flag: "🇹🇭", NameZH: "泰国", NameEN: "Thailand",
		Pattern: `🇹🇭|泰国|泰國|曼谷|(?i:thailand|bangkok)|` + code("TH|THA")},
	{Code: "VN", Flag: "🇻🇳", NameZH: "越南", NameEN: "Vietnam",
		Pattern: `🇻🇳|越南|胡志明|河内|河內|(?i:viet ?nam|hanoi)|` + code("VN|VNM")},
	{Code: "MY", Flag: "🇲🇾", NameZH: "马来西亚", NameEN: "Malaysia",
		Pattern: `🇲🇾|马来西亚|馬來西亞|吉隆坡|(?i:malaysia|kuala ?lumpur)|` + code("MY|MYS")},
	{Code: "PH", Flag: "🇵🇭", NameZH: "菲律宾", NameEN: "Philippines",
		Pattern: `🇵🇭|菲律宾|菲律賓|马尼拉|馬尼拉|(?i:philippines|manila)|` + code("PH|PHL")},
	{Code: "AU", Flag: "🇦🇺", NameZH: "澳大利亚", NameEN: "Australia",
		Pattern: `🇦🇺|澳大利亚|澳大利亞|澳洲|悉尼|墨尔本|墨爾本|(?i:australia|sydney|melbourne)|` + code("AU|AUS")},
	{Code: "AR", Flag: "🇦🇷", NameZH: "阿根廷", NameEN: "Argentina",
		Pattern: `🇦🇷|阿根廷|(?i:argentina)|` + code("AR|ARG")},
	{Code: "BR", Flag: "🇧🇷", NameZH: "巴西", NameEN: "Brazil",
		Pattern: `🇧🇷|巴西|圣保罗|聖保羅|(?i:brazil|s[aã]o ?paulo)|` + code("BR|BRA")},
	{Code: "AE", Flag: "🇦🇪", NameZH: "阿联酋", NameEN: "United Arab Emirates",
		Pattern: `🇦🇪|阿联酋|阿聯酋|迪拜|杜拜|(?i:emirates|dubai)|` + code("AE|UAE")},
	// Last of the countries: "中国" often travels with another place ("中国香港"), and "CN2"
	// names a carrier's line, not the region.
	{Code: "CN", Flag: "🇨🇳", NameZH: "中国", NameEN: "China",
		Pattern: `🇨🇳|中国|中國|大陆|大陸|回国|回國|(?i:mainland ?china)|(?<![A-Za-z])CN(?![A-Za-z0-9])`},
	{Code: OtherRegion, Flag: "🌐", NameZH: "其他地区", NameEN: "Other regions"},
}

var regionsByCode = func() map[string]*Region {
	byCode := make(map[string]*Region, len(Regions))
	for _, region := range Regions {
		if region.Pattern != "" {
			region.re = regexp2.MustCompile(region.Pattern, regexp2.None)
		}
		byCode[region.Code] = region
	}
	return byCode
}()

// RegionOf returns the code of the first region whose pattern finds name, or OtherRegion.
func RegionOf(name string) string {
	for _, region := range Regions {
		if region.re == nil {
			continue
		}
		if found, _ := region.re.MatchString(name); found {
			return region.Code
		}
	}
	return OtherRegion
}

func regionByCode(code string) (*Region, error) {
	region, ok := regionsByCode[code]
	if !ok {
		return nil, fmt.Errorf("unknown region %q", code)
	}
	return region, nil
}

// groupName names a region's group in the plan's language.
func (r *Region) groupName(locale string) string {
	if locale == localeEN {
		return r.Flag + " " + r.NameEN
	}
	return r.Flag + " " + r.NameZH
}
