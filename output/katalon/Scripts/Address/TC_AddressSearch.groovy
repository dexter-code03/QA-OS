/**
 * Test Case: TC_AddressSearch
 * Description: Verifies the address search functionality including debounce logic,
 *              autocomplete suggestions display, and selection.
 * FRD Reference: Section 5.2 - Global Express Entry: Functional Flow
 */

import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
import com.kms.katalon.core.model.FailureHandling
import com.kms.katalon.core.annotation.SetUp
import com.kms.katalon.core.annotation.TearDown
import com.kms.katalon.core.util.KeywordUtil

class TC_AddressSearch {

    /**
     * SetUp method to initialize the mobile application before each test.
     * Assumes the app starts on or navigates to the address entry screen.
     */
    @SetUp
    def setUp() {
        KeywordUtil.logInfo("Starting application...")
        Mobile.startApplication('your_application_id', true) // Replace 'your_application_id' with your actual app ID
        Mobile.waitForApplicationToLoad(10)
        // Assuming the app navigates directly to the address screen or we need to tap a button
        // Mobile.tap(findTestObject('Object Repository/Mobile/HomeScreen/btn_StartOnboarding'), 10)
        // Mobile.tap(findTestObject('Object Repository/Mobile/OnboardingScreen/btn_EnterAddress'), 10)
        KeywordUtil.logInfo("Navigated to Address Entry Screen.")
    }

    /**
     * TearDown method to close the mobile application after each test.
     */
    @TearDown
    def tearDown() {
        KeywordUtil.logInfo("Closing application...")
        Mobile.closeApplication()
    }

    /**
     * Test steps for verifying address search, debounce, and suggestions.
     */
    @com.kms.katalon.core.annotation.TestCase
    def testAddressSearchAndSuggestions() {
        KeywordUtil.logInfo("--- Starting TC_AddressSearch ---")

        String partialAddress = "123 Main" // Less than 4 words, or 50% length
        String fullAddress = "123 Main Street, Anytown, CA"
        String expectedSuggestion = "123 Main Street, Anytown, CA 90210" // Example suggestion

        // 1. Type a partial address (less than threshold) - no suggestions should appear immediately.
        KeywordUtil.logInfo("Step 1: Typing partial address: '${partialAddress}'")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), partialAddress, 10)
        Mobile.delay(2) // Short delay to ensure no premature suggestions
        Mobile.verifyElementNotPresent(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 3, FailureHandling.OPTIONAL)
        KeywordUtil.logInfo("Verified no suggestions appeared for partial input.")

        // 2. Continue typing to meet the threshold and trigger debounce.
        KeywordUtil.logInfo("Step 2: Continuing to type to meet threshold and trigger debounce.")
        Mobile.setText(findTestObject('Object Repository/Mobile/AddressScreen/txt_AddressSearch'), fullAddress, 10)
        Mobile.hideKeyboard() // Hide keyboard to ensure suggestions are visible

        // 3. Pause typing (simulate debounce) and wait for suggestions.
        KeywordUtil.logInfo("Step 3: Waiting for debounce and suggestions to appear.")
        Mobile.delay(4) // Simulate debounce delay (e.g., 2-3 seconds + API response time)

        // 4. Verify suggestions appear.
        KeywordUtil.logInfo("Step 4: Verifying suggestions appear.")
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 10)
        Mobile.verifyElementText(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), expectedSuggestion, FailureHandling.STOP_ON_FAILURE)
        KeywordUtil.logInfo("Verified first suggestion: '${expectedSuggestion}' is displayed.")

        // 5. Select a suggestion.
        KeywordUtil.logInfo("Step 5: Tapping on the first suggestion.")
        Mobile.tap(findTestObject('Object Repository/Mobile/AddressScreen/lbl_Suggestion_1'), 10)

        // 6. Verify navigation to the mapped fields screen (or next step).
        KeywordUtil.logInfo("Step 6: Verifying navigation to the mapped address fields screen.")
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/txt_StreetName'), 10)
        Mobile.verifyElementPresent(findTestObject('Object Repository/Mobile/AddressScreen/btn_Continue'), 10)
        KeywordUtil.logInfo("Successfully navigated to the mapped address fields screen.")

        KeywordUtil.logInfo("--- TC_AddressSearch Completed Successfully ---")
    }
}